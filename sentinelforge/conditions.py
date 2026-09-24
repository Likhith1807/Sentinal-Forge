"""Deterministic, evidence-carrying condition finder.

Given the text of a report this module locates - with exact character offsets - every span that
could state a detection condition: how many of what, inside what time window, over which log
fields, and with which extra qualifiers. It makes NO decision about which candidate is "the" rule;
that is `reconcile.py`'s job, where a model's proposal is checked against these candidates.

Why a separate, rule-based pass exists at all
---------------------------------------------
A learned extractor answers "what does this report mean?" with a score. The audit of the
fine-tuned pipeline showed that score is not enough to authorise compilation: it turned
"90 seconds" into 5,400 seconds (a separate unit classifier was never checked against the text),
and it compiled a port-scan report as a password spray (its 11-field vocabulary cannot even
*name* a destination port). Every value that ends up in a compiled rule must therefore be
traceable to characters in the report, read out of those characters by code that cannot
hallucinate, and any disagreement with the model surfaces as a rejection - never a silent pick.

This is *quote verification*: the span exists and parses. It is not proof that the passage
means what the extractor says; that residual is reported as an explicit uncertainty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from .behaviours import DISTINCT_ACCOUNTS, DISTINCT_HOSTS, EVENT_COUNT
from .textutil import NUMBER, parse_number, plain, sentence_of, sentences

UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}
_UNIT_RE = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)"


def canonical_unit(word: str) -> str:
    w = word.lower()
    if w == "s" or w.startswith("sec"):
        return "seconds"
    if w.startswith("min"):
        return "minutes"
    if w.startswith("h"):
        return "hours"
    if w.startswith("d"):
        return "days"
    raise ValueError(word)


@dataclass(frozen=True)
class Evidence:
    """A span of the ORIGINAL report. `quoteVerified` says only that the quote reproduces the report's
    characters at those offsets; it does not certify that the passage supports any interpretation."""
    start: int
    end: int
    quote: str
    sentenceStart: int
    sentenceEnd: int
    quoteVerified: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def _evidence(text: str, sents, start: int, end: int) -> Evidence:
    s, e = sentence_of(sents, start)
    return Evidence(start, end, text[start:end], s, e, quoteVerified=True)


def verify_quote(report_text: str, ev: dict | Evidence) -> bool:
    """Quote verification: do these offsets reproduce this quote in THIS report? (Not a semantic check.)"""
    d = ev if isinstance(ev, dict) else ev.to_dict()
    try:
        return report_text[d["start"]:d["end"]] == d["quote"] and d["end"] > d["start"]
    except (KeyError, TypeError):
        return False


@dataclass
class CountCandidate:
    value: int
    form: str                       # or-more | at-least | no-fewer | plus | more-than | bare | upper-bound
    comparator: str                 # gte | lt | unspecified
    subject: str                    # the counted noun phrase, as written
    semantics: str | None           # event_count | distinct_accounts | distinct_hosts | None (unrecognised object)
    eventKind: str | None           # login_failure | login_success | None
    unknownObject: str | None       # the counted thing when it is outside every supported behaviour
    evidence: Evidence
    ruleSentence: bool = False


@dataclass
class WindowCandidate:
    amount: float
    unit: str                       # seconds | minutes | hours | days
    seconds: float
    cue: str | None                 # the word that marks it as a window (within / inside / window ...)
    approximate: bool
    evidence: Evidence
    ruleSentence: bool = False


@dataclass
class FieldMention:
    raw: str
    field: str | None               # canonical schema field, or None when the report asks for something unknown
    evidence: Evidence
    form: str                       # code | phrase
    incidental: bool = False        # the sentence says this attribute is NOT part of the rule


@dataclass
class Qualifier:
    kind: str
    evidence: Evidence


@dataclass
class BehaviourSignal:
    behaviourId: str
    cues: list                      # list[Evidence]
    missing: list                   # human-readable cues that were NOT found

    @property
    def complete(self) -> bool:
        return not self.missing


@dataclass
class ConditionReport:
    counts: list = field(default_factory=list)
    windows: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    qualifiers: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    ruleSentences: list = field(default_factory=list)      # [(start, end)]
    caveats: list = field(default_factory=list)            # things the passage says the rule cannot faithfully measure
    methodLiterals: list = field(default_factory=list)     # concrete auth methods named inside rule sentences

    def to_dict(self) -> dict:
        return {
            "counts": [asdict(c) for c in self.counts],
            "windows": [asdict(w) for w in self.windows],
            "fields": [asdict(f) for f in self.fields],
            "qualifiers": [asdict(q) for q in self.qualifiers],
            "signals": [{"behaviourId": s.behaviourId, "cues": [c.to_dict() for c in s.cues],
                         "missing": s.missing, "complete": s.complete} for s in self.signals],
            "ruleSentences": [list(s) for s in self.ruleSentences],
            "caveats": [asdict(c) for c in self.caveats],
            "methodLiterals": [asdict(c) for c in self.methodLiterals],
        }


# --------------------------------------------------------------------------------------- counts

_N = NUMBER
_LEAD = r"(?<![\w.])"
_COUNT_FORMS = [
    ("plus", re.compile(rf"{_LEAD}(?P<n>{_N})\s*\+(?!\w)")),
    ("at-least", re.compile(rf"(?:>=|≥|=>)\s*(?P<n>{_N})\b")),
    ("at-least", re.compile(rf"\b(?:(?:reach|meet)(?:s|es|ed|ing)?\s+or\s+exceed(?:s|ed|ing)?|(?:equal\s+to|equals)\s+or\s+(?:greater|more)\s+than|"
                            rf"(?:greater|more)\s+than\s+or\s+equal\s+to)\s+(?P<n>{_N})\b")),
    ("at-least", re.compile(rf"\b(?:fire|alert|trigger|escalate|page|raise)(?:s|d)?\s+(?:at|on|once\s+(?:it\s+)?reaches|when\s+(?:it\s+)?reaches|when\s+the\s+count\s+(?:reaches|hits))\s+(?P<n>{_N})\b")),
    ("times-x", re.compile(rf"{_LEAD}(?P<n>{_N})\s?[x×](?![a-z\d])")),
    ("or-more", re.compile(rf"{_LEAD}(?P<n>{_N})\s+(?:or|and)\s+(?:more|greater|above|higher|over)\b")),
    ("at-least", re.compile(rf"\bat\s+least\s+(?P<n>{_N})\b")),
    ("at-least", re.compile(rf"\b(?:a\s+)?minimum\s+(?:of\s+)?(?P<n>{_N})\b")),
    ("no-fewer", re.compile(rf"\bno\s+(?:fewer|less)\s+than\s+(?P<n>{_N})\b")),
    ("more-than", re.compile(rf"\b(?:more|greater)\s+than\s+(?P<n>{_N})\b")),
    ("more-than", re.compile(rf"\b(?:over|exceed(?:s|ed|ing)?)\s+(?P<n>{_N})\b")),
    ("upper-bound", re.compile(rf"\b(?:fewer|less)\s+than\s+(?P<n>{_N})\b")),
    ("upper-bound", re.compile(rf"\b(?:at\s+most|no\s+more\s+than|up\s+to)\s+(?P<n>{_N})\b")),
    ("bare", re.compile(rf"{_LEAD}(?P<n>{_N})(?=\s+(?:distinct|different|separate|unique|failed|failures?|failing))")),
]
_LABELLED = re.compile(
    rf"(?m)^[\s|*\-]*(?P<label>[a-z][a-z _\-]{{0,40}}?(?:threshold|count|accounts|users|usernames|hosts|machines|workstations|failures|attempts))"
    rf"\s*[|:=]\s*[|\s]*(?P<n>{_N})\s*(?:\||$)")
_NEGATIVE_BEFORE = re.compile(r"(?:^|[\s(\[:,;|])-$")
_FOLLOWED_BY_TIME = re.compile(rf"^\s*-?\s*{_UNIT_RE}\b")
_PHRASE_STOP = re.compile(
    r"\b(?:within|inside|in|over|during|per|across|before|after|then|followed|and|while|from|against|"
    r"for|on|at|by|that|which|where|without|of|to|is|are|was|were|has|have|had)\b|[,;:.()|\n]")

_FAIL = re.compile(r"\bfail|\bfailure|\bunsuccessful|\bdenied\b|\brejected\b")
_SUCC = re.compile(r"\bsucce|\bsuccessful")
_DISTINCT = re.compile(r"\b(?:distinct|different|separate|unique|unrelated|multiple|various|several)\b")
_ACCOUNT_NOUN = re.compile(r"\b(?:accounts?|account_ids?|users?|usernames?|identit(?:y|ies)|principals?|logins? names?|user ids?)\b")
_HOST_NOUN = re.compile(
    r"\b(?:source_hosts?|(?:source |originating |client )?hosts?|machines?|systems?|workstations?|endpoints?|devices?|"
    r"computers?|servers?|gateways?|nodes?|ips?|ip addresses)\b")
_EVENT_NOUN = re.compile(
    r"\b(?:times?|attempts?|tries|events?|occasions|logins?|log-?ins?|sign-?ins?|authentications?|"
    r"authentication attempts|failures?|failed|misses)\b")
_KNOWN_NOUN_WORDS = {"account", "accounts", "user", "users", "host", "hosts", "machine", "machines", "system",
                     "systems", "login", "logins", "attempt", "attempts", "time", "times", "event", "events"}


def _nearest_kind(before: str, phrase: str, after: str) -> str | None:
    """login_failure / login_success for the thing being counted: the phrase itself first, then whichever
    of "fail*" / "succe*" sits closest to the number (so "fails to authenticate more than nine times ...
    and subsequently succeeds" reads as failures, not as an ambiguous sentence)."""
    if _FAIL.search(phrase):
        return "login_failure"
    if _SUCC.search(phrase):
        return "login_success"
    best = None
    for kind, rx in (("login_failure", _FAIL), ("login_success", _SUCC)):
        for m in rx.finditer(before):
            d = len(before) - m.end()
            if best is None or d < best[0]:
                best = (d, kind)
        for m in rx.finditer(after):
            d = m.start() + 8                              # small bias toward the words that precede the number
            if best is None or d < best[0]:
                best = (d, kind)
    return best[1] if best else None


def _head_noun(phrase: str) -> str | None:
    first = None
    for name, rx in (("account", _ACCOUNT_NOUN), ("host", _HOST_NOUN), ("event", _EVENT_NOUN)):
        m = rx.search(phrase)
        if m and (first is None or m.start() < first[1]):
            first = (name, m.start())
    return first[0] if first else None


_GROUPING_CLAUSE = re.compile(
    r"\(?\s*\b(?:per|by|for\s+each|for\s+every|grouped\s+by|group\s+by)\s+[\w_]+\s*\)?|"
    r"\b(?:for|against|on|from)\s+(?:the\s+same|one|a\s+single|a|an|each|every|any|that|this)\s+(?:source\s+|originating\s+)?[\w_]+")


def _object_from_context(back: str, forward: str) -> str | None:
    """The counted object when the number is not followed by it ("count distinct accounts ...; reaches four or more",
    "event: failed login (per account) / count: 5 or more"). Nouns in "per X" / "grouped by X" clauses describe the
    grouping, not what is counted, and are ignored. A noun introduced by distinct/different wins; otherwise the
    nearest noun before the number, otherwise the first after it."""
    back = _GROUPING_CLAUSE.sub(" ", back)
    forward = _GROUPING_CLAUSE.sub(" ", forward)
    last, last_distinct = None, None
    for name, rx in (("account", _ACCOUNT_NOUN), ("host", _HOST_NOUN), ("event", _EVENT_NOUN)):
        for m in rx.finditer(back):
            if last is None or m.start() > last[1]:
                last = (name, m.start())
            if _DISTINCT.search(back[max(0, m.start() - 14):m.start()]) and (last_distinct is None or m.start() > last_distinct[1]):
                last_distinct = (name, m.start())
    chosen = last_distinct or last
    if chosen:
        return chosen[0]
    return _head_noun(forward)


def _classify_subject(phrase: str, before: str, after: str, back_wide: str = "", forward_wide: str = "") -> tuple[str | None, str | None, str | None]:
    """-> (semantics, event kind, unknown object). The FIRST counted noun in the phrase is the head of
    what is being counted ("4 different accounts" counts accounts, not the word "different")."""
    kind = _nearest_kind(before, phrase, after)
    head = _head_noun(phrase)
    if head is None and not phrase.strip():
        # "...count distinct accounts that failed...; if the count reaches four or more" - object named earlier
        head = _object_from_context(back_wide or before[-160:], forward_wide or after[:120])
    if head is None:
        words = _DISTINCT.sub(" ", phrase).split()
        w0 = words[0] if words else ""
        plural_unknown = w0.endswith("s") and len(w0) > 3 and w0 not in _KNOWN_NOUN_WORDS
        unknown = phrase.strip() if (plural_unknown or (words and _DISTINCT.search(phrase))) else None
        return None, kind, unknown
    if head == "account":
        return DISTINCT_ACCOUNTS, kind, None
    if head == "host":
        return DISTINCT_HOSTS, kind, None
    return EVENT_COUNT, kind, None


def _find_counts(text: str, low: str, sents) -> list[CountCandidate]:
    out: list[CountCandidate] = []
    taken: list[tuple[int, int]] = []
    for form, rx in _COUNT_FORMS:
        for m in rx.finditer(low):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue                                   # an earlier, more specific form already claimed it
            if _FOLLOWED_BY_TIME.match(low[e:e + 24]):
                continue                                   # "more than 5 minutes" is a duration, not a count
            n = parse_number(m.group("n"))
            if n is None or (isinstance(n, float) and not n.is_integer()):
                continue
            n = int(n)
            negative = bool(_NEGATIVE_BEFORE.search(low[max(0, m.start("n") - 2):m.start("n")]))
            if form == "more-than" and m.group("n").lower() == "one":
                continue                                   # "more than one host" is vague narration, not a threshold
            ss, se = sentence_of(sents, s)
            tail = low[e:se]
            stop = _PHRASE_STOP.search(tail)
            phrase_end = e + (stop.start() if stop else len(tail))
            phrase = low[e:phrase_end].strip(" -\"'")
            semantics, kind, unknown = _classify_subject(phrase, low[ss:s], low[e:se], low[max(0, s - 200):s], low[e:e + 200])
            if form == "more-than":
                n += 1
            comparator = "invalid" if negative else "lt" if form == "upper-bound" else "unspecified" if form == "bare" else "gte"
            out.append(CountCandidate(
                value=n, form=form, comparator=comparator, subject=text[e:phrase_end].strip(" -\"'*`"),
                semantics=semantics, eventKind=kind, unknownObject=unknown,
                evidence=_evidence(text, sents, s, e)))
            taken.append((s, e))
    for m in _LABELLED.finditer(low):
        s, e = m.start("n"), m.end("n")
        if any(s < te and ts < e for ts, te in taken):
            continue
        n = parse_number(m.group("n"))
        if n is None or not float(n).is_integer():
            continue
        label = m.group("label").strip(" |*-")
        ss, se = sentence_of(sents, s)
        semantics, kind, unknown = _classify_subject(label, low[ss:s], low[e:se], low[max(0, s - 200):s], low[e:e + 200])
        if semantics is None and re.search(r"threshold|count", label) and not unknown:
            semantics, kind = None, kind
        out.append(CountCandidate(value=int(n), form="labelled", comparator="invalid" if _NEGATIVE_BEFORE.search(low[max(0, s - 2):s]) else "gte",
                                  subject=label, semantics=semantics, eventKind=kind, unknownObject=unknown,
                                  evidence=_evidence(text, sents, s, e)))
        taken.append((s, e))
    out.sort(key=lambda c: c.evidence.start)
    return out


# -------------------------------------------------------------------------------------- windows

_APPROX = re.compile(r"(?:\babout|\broughly|\bapproximately|\baround|\bnearly|\balmost|\bsome|~|\bjust\s+under|\bwell\s+under|\bjust\s+over|\bwell\s+over|\ba\s+little\s+under)\s*$")
_WINDOW_CUE = re.compile(
    r"(?:\b(within|inside|in|over|during|across|per|every|each|for|last|past|rolling|sliding|preceding|previous|prior|trailing|under|throughout|next)\s+"
    r"(?:(?:a|the|any|one|an)\s+)?(?:rolling\s+|sliding\s+|single\s+)?|"
    r"\b(less\s+than|no\s+more\s+than|not\s+more\s+than|inside\s+of)\s+(?:a\s+)?|"
    r"\b(?:time\s+)?(?:window|interval|period|timeframe|duration|span)\s*[|:=]\s*[|\s]*|"
    r"\b(window|period|interval|span|timeframe)\s+of\s+(?:a\s+|about\s+)?)$")
_AFTER_WINDOW = re.compile(r"^\s*-?\s*(?:rolling\s+|sliding\s+)?(window|period|interval|span|timeframe|time frame)\b")
_TIME = re.compile(rf"(?<![\w.:])(?P<n>{_N})(?:\s*-\s*|\s+)(?P<u>{_UNIT_RE})\b")
_TIME_GLUED = re.compile(r"(?<![\w.:])(?P<n>\d+(?:\.\d+)?)(?P<u>s|sec|secs|min|mins|h|hr|hrs)\b")
_A_UNIT = re.compile(r"\b(?P<art>an?|half an?)\s+(?P<u>hour|minute|second|day)\b")
_NARRATIVE_AFTER = re.compile(r"^\s*(?:later|apart|ago|earlier)\b")
# "5 minutes before/after X" is narration unless a window word ("within 30 minutes before a success") frames it
_NARRATIVE_WEAK = re.compile(r"^\s*(?:before|after|old|into|prior)\b")
_STRONG_CUES = {"within", "inside", "rolling", "sliding", "window", "period", "interval", "span", "timeframe",
                "preceding", "previous", "last", "past", "under", "label"}


def _find_windows(text: str, low: str, sents) -> list[WindowCandidate]:
    out: list[WindowCandidate] = []
    for m in _TIME.finditer(low):
        n = parse_number(m.group("n"))
        if n is None:
            continue
        s, e = m.span()
        before, after = low[max(0, s - 32):s], low[e:e + 24]
        if _NARRATIVE_AFTER.match(after):
            continue
        cue_m = _WINDOW_CUE.search(before)
        aft_m = _AFTER_WINDOW.match(after)
        hyphenated = "-" in low[m.end("n"):m.start("u")]
        cue = (cue_m.group(1) or cue_m.group(2) or cue_m.group(3) or "label") if cue_m else ("window" if aft_m else ("hyphenated" if hyphenated else None))
        if _NARRATIVE_WEAK.match(after) and cue not in _STRONG_CUES:
            continue                                       # "5 minutes before the login": narration, not a window
        unit = canonical_unit(m.group("u"))
        out.append(WindowCandidate(amount=float(n), unit=unit, seconds=float(n) * UNIT_SECONDS[unit],
                                   cue=cue, approximate=bool(_APPROX.search(before)),
                                   evidence=_evidence(text, sents, s, e)))
    for m in _TIME_GLUED.finditer(low):
        s, e = m.span()
        if any(w.evidence.start <= s < w.evidence.end for w in out):
            continue
        before = low[max(0, s - 32):s]
        cue_m = _WINDOW_CUE.search(before)
        unit = canonical_unit(m.group("u"))
        n = float(m.group("n"))
        out.append(WindowCandidate(amount=n, unit=unit, seconds=n * UNIT_SECONDS[unit],
                                   cue=(cue_m.group(1) or cue_m.group(2) or cue_m.group(3) or "label") if cue_m else "abbreviated",
                                   approximate=bool(_APPROX.search(before)), evidence=_evidence(text, sents, s, e)))
    for m in _A_UNIT.finditer(low):
        s, e = m.span()
        before = low[max(0, s - 32):s]
        cue_m = _WINDOW_CUE.search(before)
        if not cue_m and not _AFTER_WINDOW.match(low[e:e + 20]):
            continue
        if any(w.evidence.start <= s < w.evidence.end for w in out):
            continue
        amount = 0.5 if m.group("art").startswith("half") else 1.0
        unit = canonical_unit(m.group("u"))
        out.append(WindowCandidate(amount=amount, unit=unit, seconds=amount * UNIT_SECONDS[unit],
                                   cue=(cue_m.group(1) or cue_m.group(2) or cue_m.group(3) or "label") if cue_m else "window",
                                   approximate=bool(_APPROX.search(before)), evidence=_evidence(text, sents, s, e)))
    out.sort(key=lambda w: w.evidence.start)
    return out


# --------------------------------------------------------------------------------------- fields

LOG_FIELD_NAMES = {"event_id", "timestamp", "account_id", "event_type", "source_host", "source_ip", "auth_method",
                   "mfa_used", "session_id"}
POLICY_FIELD_NAMES = {"expected_auth_method", "mfa_required"}

# natural-language mentions of each schema field (matched on the lower-cased plain text of one list item)
_PHRASE_ALIASES: list[tuple[str, re.Pattern]] = [(f, re.compile(p)) for f, p in [
    ("policy.mfa_required", r"polic\w*.*\bmfa\b|\bmfa\b.*(?:requirement|required|mandat)|multi-?factor.*(?:requirement|required|polic)|requires? (?:mfa|multi)"),
    ("policy.expected_auth_method", r"(?:expected|provisioned|policy|permitted|approved|assigned).*(?:method|credential|auth)|(?:method|credential).*(?:provisioned|identity polic|policy|expected)"),
    ("mfa_used", r"second factor|mfa (?:usage|used|presented|challenge)|multi-?factor|whether mfa|\bmfa\b|2fa"),
    ("auth_method", r"authentication method|auth(?:entication)? type|how the account authenticated|credential type|auth method|method used|type of credential"),
    ("source_ip", r"\bip\b|ip address"),
    ("source_host", r"\bhost\b|originating|machine|workstation|gateway|source system|came from"),
    ("timestamp", r"\btime|timestamp|when each|\bdate\b|clock"),
    ("event_type", r"outcome|succeeded or failed|success(?:/| or | and )fail|result|status|event type|type of (?:event|attempt)|whether (?:each|the) (?:attempt|login|sign)|success or failure"),
    ("session_id", r"session"),
    ("account_id", r"\baccount|\buser|identity|identifier|principal|login name"),
]]

# Only these announce a list of required log fields. Weak verbs ("requires", "reads") elsewhere in a
# report describe the narrative, and treating their objects as field requirements manufactured phantom
# "unknown fields" in supported reports.
_STRONG_FIELD_CUE = re.compile(
    r"(?:required log fields?|log fields? (?:needed|required)|fields? needed|data needed|"
    r"(?:the )?log (?:has|have) to provide|(?:the )?logs? (?:must|should) (?:provide|contain|capture|include)|"
    r"(?:evaluating|evaluate|to evaluate|to support)[^.:]{0,40}?(?:requires?|needs?|(?:has|have) to provide|must (?:capture|provide|ingest))|"
    r"(?:rule|detection)(?: logic)? (?:reads?|pulls?|ingests?|relies on|uses|consumes)(?: (?:the )?(?:fields?|following))?|"
    r"requires? the (?:log )?fields?|(?:rule|detection) (?:must|should) (?:ingest|capture|read|pull))")
_BARE_FIELD = re.compile(r"(?<![\w.`\"'])(policy\.[A-Za-z_]+|event_id|account_id|event_type|source_host|source_ip|auth_method|mfa_used|session_id|expected_auth_method|mfa_required)(?![\w])")
_CODE_TOKEN = re.compile(r"[`\"\u201c\u2018']([A-Za-z][A-Za-z0-9_.]*[A-Za-z0-9])[`\"\u201d\u2019']")
_FILLER_ITEM = re.compile(r"\b(?:any|additional|context|clarification|this|that|these|pattern|following|present|"
                          r"followed|successful|login|sign-?in|then|ticket|sample|log lines?|consistent)\b")


def _map_phrase(item: str) -> str | None:
    item = item.replace("_", " ").replace(".", " ")
    for fld, rx in _PHRASE_ALIASES:
        if rx.search(item):
            return fld
    return None


def _split_items(seg: str) -> list[tuple[int, str]]:
    items, pos = [], 0
    for m in re.finditer(r",\s*(?:and\s+)?|\s+and\s+|;\s*", seg):
        items.append((pos, seg[pos:m.start()]))
        pos = m.end()
    items.append((pos, seg[pos:]))
    return [(o, t) for o, t in items if t.strip()]


def _find_fields(text: str, low: str, sents, incidental_sents: set) -> list[FieldMention]:
    out: list[FieldMention] = []
    seen: list[tuple[int, int]] = []
    # 1. code-formatted identifiers anywhere in the report
    for m in _CODE_TOKEN.finditer(text):
        raw = m.group(1)
        low_raw = raw.lower()
        bare = low_raw.removeprefix("policy.")
        if low_raw in LOG_FIELD_NAMES:
            canon = low_raw
        elif bare in POLICY_FIELD_NAMES:
            canon = f"policy.{bare}"
        elif ("_" in low_raw or "." in low_raw) and re.fullmatch(r"[a-z][a-z0-9]*(?:[_.][a-z0-9]+)+", low_raw):
            canon = None                                   # looks like a field name the schema does not have
        else:
            continue
        s, e = m.start(1), m.end(1)
        out.append(FieldMention(raw, canon, _evidence(text, sents, s, e), "code",
                                incidental=sentence_of(sents, s) in incidental_sents))
        seen.append((s, e))
    # 1b. bare identifiers ("auth_method vs policy.expected_auth_method", "| mfa_used | false |")
    for m in _BARE_FIELD.finditer(text):
        s, e = m.start(1), m.end(1)
        if any(s < pe and ps < e for ps, pe in seen):
            continue
        raw = m.group(1)
        low_raw = raw.lower()
        bare = low_raw.removeprefix("policy.")
        canon = low_raw if low_raw in LOG_FIELD_NAMES else f"policy.{bare}" if bare in POLICY_FIELD_NAMES else None
        if canon is None:
            continue
        out.append(FieldMention(raw, canon, _evidence(text, sents, s, e), "code", incidental=sentence_of(sents, s) in incidental_sents))
        seen.append((s, e))
    # 2. natural-language field lists announced by a strong cue: "the log has to provide X, Y and Z"
    for ss, se in sents:
        if (ss, se) in incidental_sents:
            continue
        cue = _STRONG_FIELD_CUE.search(low[ss:se])
        if not cue:
            continue
        tail_start = ss + cue.end()
        raw_tail = low[tail_start:se]
        stripped = re.sub(r"^[\s:\-]*(?:the\s+following\s+fields?[:\s]*|the\s+fields?[:\s]*)?", "", raw_tail)
        stripped = re.sub(r"^(?:from|in|of|on|within)\b[^:]*:?\s*$", "", stripped)
        tail_start += len(raw_tail) - len(stripped)
        for o, item in _split_items(stripped.rstrip(". ")):
            item_clean = item.strip(" .\"'*`")
            if len(item_clean) < 3 or len(item_clean.split()) > 9 or _FILLER_ITEM.search(item_clean) and not _map_phrase(item_clean):
                continue
            if re.match(r"^(?:from|in|of|on|within|to)\b", item_clean):
                continue
            s = tail_start + o + (len(item) - len(item.lstrip()))
            e = s + len(item.strip())
            if any(s < pe and ps < e for ps, pe in seen):
                continue                                   # already captured as a code-formatted name
            out.append(FieldMention(text[s:e].strip(" .\"'*`"), _map_phrase(item_clean),
                                    _evidence(text, sents, s, e), "phrase"))
            seen.append((s, e))
    out.sort(key=lambda f: f.evidence.start)
    return out


# ---------------------------------------------------------------------------------- qualifiers

_QUALIFIERS: list[tuple[str, re.Pattern]] = [(k, re.compile(p)) for k, p in [
    ("time-of-day / business-hours restriction", r"outside (?:of )?(?:the )?(?:business|working|normal|office|regular) hours|off-?hours|after[- ]hours|weekends?|at night|overnight|during (?:business|working) hours"),
    ("geographic condition", r"geo-?locat|\bcountr(?:y|ies)\b|impossible travel|\bregions?\b|geograph"),
    ("device / client attribute", r"new device|unfamiliar|user-?agent|device id|\bbrowser\b|operating system"),
    ("account-privilege restriction", r"privileged accounts? only|only (?:for|on|when|if|against|from)\b|admin(?:istrator)? accounts? only|domain admins?"),
    ("exclusion clause", r"\bexcept(?: for| when)?\b|\bexcluding\b|\bunless\b|\bexempt\b|\bwhitelist|\ballow-?list"),
    ("rate / ratio condition", r"\bratio\b|\bpercent(?:age)?\b|\d\s*%|\brate of\b|per (?:second|minute|hour|day)\b"),
    ("grouping other than account or host", r"same (?:subnet|network|country|domain|user-?agent|device|ip(?: address)?)\b|(?:one|single) (?:subnet|network|country|domain|user-?agent|ip address)"),
    ("network / port / volume condition", r"\bports?\b|\bbytes\b|\bmb\b|\bgb\b|\bpackets?\b|\bdns\b|\bdomain names?\b|\burls?\b|command line|\bprocess(?:es)?\b|\bregistry\b|\bfile (?:path|rename)|\bfiles\b"),
    ("lockout / directory event", r"lockouts?|event 47\d\d|event id \d+|\b4740\b"),
    ("multi-condition window (a second time bound)", r"and (?:then )?(?:a )?success(?:ful)?[^.]{0,40}within \d"),
]]
# "We explicitly do NOT want an alert when 5 or more accounts fail ...": a sentence that negates the intent to alert
# states no rule even though it contains a comparator. (Bare "no" is deliberately absent: "no fewer than 5".)
_NEGATED_INTENT = re.compile(
    r"\b(?:do(?:es)?\s+not|don't|doesn't|did\s+not|should\s+not|shouldn't|must\s+not|shall\s+not|never)\b[^.;\n]{0,30}"
    r"\b(?:want|need|wish|expect|require|alert|fire|trigger|flag|page|escalate)\b|\bexplicitly\s+not\b|\bno\s+alert\b")
_RULE_CUE = re.compile(
    r"\b(?:alert|alerts|fire|fires|flag|flags|treat|trigger|triggers|raise|detect|detection|rule|escalate|notify|"
    r"requirement|criteria|logic|condition|when|count)\b|ask:")
# sentences that explicitly say a field/attribute is NOT part of the rule ("incidental", "must not depend on it")
_INCIDENTAL = re.compile(
    r"\bincidental\b|not part of the (?:pattern|rule|detection)|must not depend|should not (?:be used|depend|affect)|"
    r"do not (?:condition|factor|rely)|does not (?:influence|affect)|not relevant|is not used|not used as a condition|"
    r"\bexclude[sd]?\b|explicitly excluded|(?:varied|differed) between events|irrelevant|should not be used|"
    r"(?:continues?|continue) to ignore|not (?:required|needed) (?:for|by) the (?:rule|detection)")


# Counter-examples: "slower guessing ... is a different problem; do not alert on it here" names a duration that is
# explicitly NOT the rule. Such a sentence is not a source of rule windows (unless it also states a comparator).
_NEGATED = re.compile(r"\b(?:do not|don't|should not|shouldn't|must not|never|not alert|out of scope|different problem|"
                      r"handled by|not covered|is not the goal)\b")


def _incidental_sentences(low: str, sents) -> set:
    return {(s, e) for s, e in sents if _INCIDENTAL.search(low[s:e])}


def _rule_sentences(low: str, sents, counts, windows, incidental) -> list[tuple[int, int]]:
    """Sentences that state (rather than narrate) a detection condition: they carry a rule cue word, or they
    put a comparator count and a cued time window together. A sentence that says an attribute is
    incidental is never a source of conditions."""
    comparator_sents = {sentence_of(sents, c.evidence.start) for c in counts if c.comparator != "unspecified"}
    negated_intent = {(s, e) for s, e in sents if _NEGATED_INTENT.search(low[s:e])}
    hits = {(s, e) for s, e in sents if _RULE_CUE.search(low[s:e]) and (s, e) not in incidental
            and not (_NEGATED.search(low[s:e]) and (s, e) not in comparator_sents)}
    for c in counts:
        sent = sentence_of(sents, c.evidence.start)
        if c.comparator not in ("unspecified", "invalid") and sent not in incidental:
            hits.add(sent)                                 # a comparator ("5 or more") is how rules are stated
        if c.comparator == "invalid":
            hits.add(sent)                                 # kept as a rule sentence so the invalid number is REPORTED
    return sorted(hits - negated_intent)


def _find_qualifiers(text: str, low: str, sents, rule_sents) -> list[Qualifier]:
    out = []
    for ss, se in rule_sents:
        seg = low[ss:se]
        for kind, rx in _QUALIFIERS:
            for m in rx.finditer(seg):
                out.append(Qualifier(kind, _evidence(text, sents, ss + m.start(), ss + m.end())))
    return out


# ------------------------------------------------------------------------------- behaviour cues

_SUBJ = r"(?:(?:it|they|he|she|the (?:same )?(?:account|user|identity|attacker|actor)|one|that account)\s+)?"
_THEN_SUCCESS = re.compile(
    r"(?:then|after(?:wards| that| this)?|later|next|followed)\b[^.;]{0,40}\bsucce(?:ss|ed|eds|ssful|ssfully)\b|"
    r"(?:ended|ending|concluded|culminat\w+|finish\w+)\s+(?:by|in|with)\s+(?:a\s+)?succe|then:\s*(?:a\s+)?succe|"
    rf"(?:then|and then|and subsequently|subsequently|afterwards?|followed by(?: a)?|and)\s+(?:then\s+)?{_SUBJ}"
    r"(?:succeeds?|succeeded|succeed\w*|logs? in|logged in|log-?ins?|signs? in|signed in|authenticates?|authenticated|gets? in|"
    r"is (?:granted|authenticated|let in)|achieves (?:a )?success|(?:a )?success(?:ful)?)|"
    r"(?:before|prior to) (?:a )?success|followed by a (?:successful|success)|"
    r"succeeds? (?:on|at|after)|then (?:a )?successful|(?:next|following|subsequent|immediately following)\s+(?:record|event|login|entry|attempt|sign-?in)[^.]{0,80}succe|"
    r"immediately followed by[^.]{0,20}succe|(?:followed|preceded) by[^.]{0,30}(?:successful|success)|"
    r"(?:then|eventually|finally|ultimately)\s+(?:a\s+)?successful")
_ONE_ACCOUNT = re.compile(
    r"\bgroup(?:ed)?\s+by\s*[:|]?\s*(?:the\s+)?(?:user\s+)?(?:account|identity|user)|"
    r"\b(?:same|one|single|an?|any|every|each|per|individual)\s+[\"']?(?:user )?(?:account_id|account|identity|user|username)\b|"
    r"\bper[- ]account\b|\bagainst (?:an|one|a single|the same) account|\b(?:the|that|this)\s+(?:target |given |specific |same )?(?:user )?account\b|\bfor the (?:target )?account\b")
_ONE_HOST = re.compile(
    r"\bgroup(?:ed)?\s+by\s*[:|]?\s*(?:the\s+)?(?:source[_ ]|originating\s+)?(?:host|machine)|"
    r"\b(?:same|one|single|an?|any|every|each|per|individual)\s+(?:source[_ ]|originating\s+|client\s+)?(?:host|machine|system|workstation|endpoint|source)\b|"
    r"\bper[- ]host\b|\bsource[_ ]host\b|\bone host\b")
_MISMATCH = re.compile(
    r"differs?\b|different from|does not (?:match|equal|use|conform)|do not (?:match|use|equal|conform)|doesn't (?:match|use)|"
    r"mismatch|deviat|diverg|other than|rather than|instead of|not the (?:expected|approved|provisioned)|unexpected|not (?:equal|the same)|"
    r"(?:is|are|was)(?: not|n't) the one|not (?:the )?same(?: as| like)?|inconsistent with|contradict\w*|conflicts? with|does not correspond|not what")
_METHOD_WORD = re.compile(r"\b(?:method|credential|auth_method|authentication type|auth type)\b")
_EXPECTED_WORD = re.compile(r"\b(?:expected|approved|provisioned|assigned|registered|permitted|polic\w+|identity (?:export|system|store|provider|records?)|defined|"
                            r"lists?|listed|on record|recorded for|configured|supposed to|meant to|should use)\b")
_METHOD_LITERAL = re.compile(r"\b(?:password|passwords|token|certificate|certificates|kerberos|ntlm|smart ?card|passkey)\b")
_AUTH_FIELDS_PAIR = ("auth_method", "policy.expected_auth_method")
# A sentence that compares against the expected/approved method (or gives an example) is stating the general
# rule; concrete method names in it are illustration, not a direction-specific condition.
_GENERAL_COMPARISON = re.compile(
    r"expected_auth_method|\b(?:expected|approved|provisioned|assigned|registered|permitted|policy|identity export|defined)\b|"
    r"\be\.g\.|\bfor example\b|\bsuch as\b|\bi\.e\.|differs?\b|mismatch|other than|rather than|instead of|regardless of direction")


def _auth_mismatch_cues(text: str, low: str, sents, rule_sents, fields) -> list:
    """A sentence states the B4 comparison when it pairs a mismatch word with the method AND an expected/policy
    reference; naming both schema fields side by side in a field list counts as corroboration."""
    cues = []
    for s, e in rule_sents:
        seg = low[s:e]
        m1, m2, m3 = _MISMATCH.search(seg), _METHOD_WORD.search(seg), _EXPECTED_WORD.search(seg)
        if m1 and m2 and m3:
            cues.append(_evidence(text, sents, s + m1.start(), s + m1.end()))
    if not cues:
        names = {f.field for f in fields if f.field and not f.incidental}
        if set(_AUTH_FIELDS_PAIR) <= names:
            f = next(f for f in fields if f.field == "policy.expected_auth_method")
            cues.append(f.evidence)
    return cues


_NO_MFA = re.compile(
    r"without (?:using |a |any |an |the )*(?:second factor|mfa|multi-?factor|2fa)|not use[sd]? (?:a |the )?(?:second factor|multi-?factor|mfa)|lack(?:s|ed|ing)? (?:a |any |an )?(?:second factor|mfa|multi-?factor)|no (?:mfa|multi-?factor(?: authentication)?|second factor)|(?:mfa|second factor|multi-?factor)\s+(?:was |is |were )?"
    r"(?:not (?:used|presented|satisfied|performed)|absent|missing|skipped|bypassed)|"
    r"(?:no|without|lacking|lacks|missing)\s+(?:a\s+)?(?:mfa|second factor|multi-?factor)|mfa_used[^.]{0,40}(?:false|not)|"
    r"mfa[- ]bypass|no second factor|not use[sd]? (?:mfa|a second factor|multi-?factor)")
_MFA_REQUIRED = re.compile(
    r"(?:requires?|required|requirement|mandat\w+)[^.]{0,40}(?:mfa|multi-?factor|second factor)|"
    r"(?:mfa|multi-?factor|second factor)[^.]{0,40}(?:required|requirement|mandat|polic)|mfa_required|polic\w+[^.]{0,30}mfa")
_OVERLAP = re.compile(r"concurrent|simultaneous|at the same time|overlap|live sessions?|active sessions?|parallel")


def _signals(text: str, low: str, sents, rule_sents, counts, fields, incidental) -> list[BehaviourSignal]:
    def ev(rx, scope=None):
        return [_evidence(text, sents, s + m.start(), s + m.end())
                for s, e in (rule_sents if scope is None else scope) for m in rx.finditer(low[s:e])]
    narrated = [(s, e) for s, e in sents if (s, e) not in incidental]

    gte = [c for c in counts if c.comparator == "gte" and c.ruleSentence]
    by_sem: dict = {}
    for c in gte:
        by_sem.setdefault(c.semantics, []).append(c)
    sig: list[BehaviourSignal] = []

    cues, missing = [], []
    ec = [c for c in by_sem.get(EVENT_COUNT, []) if c.eventKind == "login_failure"]
    (cues.append(ec[0].evidence) if ec else missing.append("a minimum number of FAILED login events"))
    then = ev(_THEN_SUCCESS) or ev(_THEN_SUCCESS, narrated)      # prefer the sentence that states the rule, not a title
    (cues.append(then[0]) if then else missing.append("a successful login that follows the failures"))
    sig.append(BehaviourSignal("repeated-failed-login-then-success", cues, missing))

    cues, missing = [], []
    da = [c for c in by_sem.get(DISTINCT_ACCOUNTS, []) if c.eventKind in ("login_failure", None)]
    (cues.append(da[0].evidence) if da else missing.append("a minimum number of DISTINCT ACCOUNTS"))
    host = ev(_ONE_HOST)
    (cues.append(host[0]) if host else missing.append("grouping by one source host"))
    if da and not any(c.eventKind == "login_failure" for c in da):
        missing.append("failed (not successful) attempts")
    sig.append(BehaviourSignal("password-spray-across-accounts", cues, missing))

    cues, missing = [], []
    dh = [c for c in by_sem.get(DISTINCT_HOSTS, []) if c.eventKind in ("login_success", None)]
    (cues.append(dh[0].evidence) if dh else missing.append("a minimum number of DISTINCT HOSTS"))
    acct = ev(_ONE_ACCOUNT)
    (cues.append(acct[0]) if acct else missing.append("grouping by one account"))
    sig.append(BehaviourSignal("multi-host-authentication", cues, missing))

    cues, missing = [], []
    am = _auth_mismatch_cues(text, low, sents, narrated, fields)
    (cues.extend(am[:1]) if am else missing.append("an authentication method that differs from the expected one"))
    sig.append(BehaviourSignal("auth-method-policy-violation", cues, missing))

    cues, missing = [], []
    nm, mr = ev(_NO_MFA, narrated), ev(_MFA_REQUIRED, narrated)
    (cues.extend(nm[:1]) if nm else missing.append("a successful login without MFA"))
    (cues.extend(mr[:1]) if mr else missing.append("an MFA requirement in policy"))
    sig.append(BehaviourSignal("mfa-missing-on-required-account", cues, missing))
    return sig


def _caveats(text: str, low: str, sents) -> list[Qualifier]:
    """Wording that promises more than the compiled rule measures. Not a rejection: the rule is still the
    right approximation, but the reviewer must be told what it does NOT check."""
    return [Qualifier("report mentions overlap / concurrency the rule cannot measure", _evidence(text, sents, m.start(), m.end()))
            for m in list(_OVERLAP.finditer(low))[:3]]


# ---------------------------------------------------------------------------------- public API

def find_conditions(text: str) -> ConditionReport:
    low = plain(text)
    sents = sentences(text)
    counts = _find_counts(text, low, sents)
    windows = _find_windows(text, low, sents)
    incidental = _incidental_sentences(low, sents)
    rule_sents = _rule_sentences(low, sents, counts, windows, incidental)
    rs = set(rule_sents)
    for c in counts:
        c.ruleSentence = sentence_of(sents, c.evidence.start) in rs
    for w in windows:
        w.ruleSentence = sentence_of(sents, w.evidence.start) in rs
    fields = _find_fields(text, low, sents, incidental)
    literals = [Qualifier("names a specific authentication method in the rule",
                          _evidence(text, sents, s + m.start(), s + m.end()))
                for s, e in rule_sents if not _GENERAL_COMPARISON.search(low[s:e])
                for m in _METHOD_LITERAL.finditer(low[s:e])]
    return ConditionReport(
        counts=counts, windows=windows, methodLiterals=literals,
        fields=fields,
        qualifiers=_find_qualifiers(text, low, sents, rule_sents),
        signals=_signals(text, low, sents, rule_sents, counts, fields, incidental),
        ruleSentences=rule_sents, caveats=_caveats(text, low, sents))
