"""Generalise the condition finder along the failure CLASSES the frozen holdout v1 exposed (never its texts):
 safety   negative counts, hedged / uncued alternative durations, negated intent ("we do NOT want an alert when ...")
 recall   count forms (5x, >= N, reaches or exceeds N, minimum N, fire at N, "ten (10)"), glued / bounded / labelled
          windows ("2min", "under 2 min", "| Window | 45 seconds |"), key/value + table rows, cross-line object lookup,
          bare field identifiers, more ways of saying "then it succeeds", "differs from what the identity system lists",
          "without using MFA".
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
p = REPO / "sentinelforge" / "conditions.py"
s = p.read_text(encoding="utf-8")


def sub(old, new, count=1):
    global s
    assert old in s, "MISSING: " + old[:90]
    s = s.replace(old, new, count)


# ---------------------------------------------------------------------------------------------- counts
sub(r'''    ("plus", re.compile(rf"{_LEAD}(?P<n>{_N})\s*\+(?!\w)")),''',
    r'''    ("plus", re.compile(rf"{_LEAD}(?P<n>{_N})\s*\+(?!\w)")),
    ("at-least", re.compile(rf"(?:>=|≥|=>)\s*(?P<n>{_N})\b")),
    ("at-least", re.compile(rf"\b(?:(?:reach|meet)(?:es|ed|ing)?\s+or\s+exceed(?:s|ed|ing)?|(?:equal\s+to|equals)\s+or\s+(?:greater|more)\s+than|"
                            rf"(?:greater|more)\s+than\s+or\s+equal\s+to)\s+(?P<n>{_N})\b")),
    ("at-least", re.compile(rf"\b(?:fire|alert|trigger|escalate|page|raise)(?:s|d)?\s+(?:at|on|once\s+(?:it\s+)?reaches|when\s+(?:it\s+)?reaches|when\s+the\s+count\s+(?:reaches|hits))\s+(?P<n>{_N})\b")),
    ("times-x", re.compile(rf"{_LEAD}(?P<n>{_N})\s?[x×](?![a-z\d])")),''')
sub(r'''    ("at-least", re.compile(rf"\b(?:a\s+)?minimum\s+of\s+(?P<n>{_N})\b")),''',
    r'''    ("at-least", re.compile(rf"\b(?:a\s+)?minimum\s+(?:of\s+)?(?P<n>{_N})\b")),''')
# labelled rows: "| Failure threshold | 6 |", "distinct accounts | 4", "hosts: 3"
sub(r'''    ("bare", re.compile(rf"{_LEAD}(?P<n>{_N})(?=\s+(?:distinct|different|separate|unique|failed|failures?|failing))")),
]''',
    r'''    ("bare", re.compile(rf"{_LEAD}(?P<n>{_N})(?=\s+(?:distinct|different|separate|unique|failed|failures?|failing))")),
]
_LABELLED = re.compile(
    rf"(?m)^[\s|*\-]*(?P<label>[a-z][a-z _\-]{{0,40}}?(?:threshold|count|accounts|users|usernames|hosts|machines|workstations|failures|attempts))"
    rf"\s*[|:=]\s*[|\s]*(?P<n>{_N})\s*(?:\||$)")
_NEGATIVE_BEFORE = re.compile(r"(?:^|[\s(\[:,;|])-$")''')
sub(r'''            if _FOLLOWED_BY_TIME.match(low[e:e + 24]):
                continue                                   # "more than 5 minutes" is a duration, not a count
            n = parse_number(m.group("n"))
            if n is None or (isinstance(n, float) and not n.is_integer()):
                continue
            n = int(n)''',
    r'''            if _FOLLOWED_BY_TIME.match(low[e:e + 24]):
                continue                                   # "more than 5 minutes" is a duration, not a count
            n = parse_number(m.group("n"))
            if n is None or (isinstance(n, float) and not n.is_integer()):
                continue
            n = int(n)
            negative = bool(_NEGATIVE_BEFORE.search(low[max(0, m.start("n") - 2):m.start("n") + 1]))''')
sub(r'''            semantics, kind, unknown = _classify_subject(phrase, low[ss:s], low[e:se])
            if form == "more-than":
                n += 1
            comparator = "lt" if form == "upper-bound" else "unspecified" if form == "bare" else "gte"''',
    r'''            semantics, kind, unknown = _classify_subject(phrase, low[ss:s], low[e:se], low[max(0, s - 200):s], low[e:e + 200])
            if form == "more-than":
                n += 1
            comparator = "invalid" if negative else "lt" if form == "upper-bound" else "unspecified" if form == "bare" else "gte"''')
sub(r'''            taken.append((s, e))
    out.sort(key=lambda c: c.evidence.start)
    return out


# -------------------------------------------------------------------------------------- windows''',
    r'''            taken.append((s, e))
    for m in _LABELLED.finditer(low):
        s, e = m.start("n"), m.end("n")
        if any(s < te and ts < e for ts, te in taken):
            continue
        n = parse_number(m.group("n"))
        if n is None or not float(n).is_integer():
            continue
        label = m.group("label").strip(" |*-")
        semantics, kind, unknown = _classify_subject(label, low[max(0, s - 120):s], "", low[max(0, s - 200):s], "")
        if semantics is None and re.search(r"threshold|count", label) and not unknown:
            semantics, kind = None, kind
        out.append(CountCandidate(value=int(n), form="labelled", comparator="invalid" if _NEGATIVE_BEFORE.search(low[max(0, s - 2):s + 1]) else "gte",
                                  subject=label, semantics=semantics, eventKind=kind, unknownObject=unknown,
                                  evidence=_evidence(text, sents, s, e)))
        taken.append((s, e))
    out.sort(key=lambda c: c.evidence.start)
    return out


# -------------------------------------------------------------------------------------- windows''')
# noun classes: "time", "run"
sub(r'''_EVENT_NOUN = re.compile(
    r"\b(?:times|attempts?|tries|events?|occasions|logins?|log-?ins?|sign-?ins?|authentications?|"''',
    r'''_EVENT_NOUN = re.compile(
    r"\b(?:times?|attempts?|tries|events?|occasions|logins?|log-?ins?|sign-?ins?|authentications?|"''')
# subject classification: wider look-back / look-forward for the counted object, ignoring grouping clauses
sub(r'''def _classify_subject(phrase: str, before: str, after: str) -> tuple[str | None, str | None, str | None]:''',
    r'''_GROUPING_CLAUSE = re.compile(r"\(?\s*\b(?:per|by|for\s+each|for\s+every|grouped\s+by|group\s+by)\s+[\w_]+\s*\)?")


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


def _classify_subject(phrase: str, before: str, after: str, back_wide: str = "", forward_wide: str = "") -> tuple[str | None, str | None, str | None]:''')
sub(r'''    if head is None and not phrase.strip():
        # "...count distinct accounts that failed...; if the count reaches four or more" - object named earlier
        back, last, last_distinct = before[-160:], None, None
        for name, rx in (("account", _ACCOUNT_NOUN), ("host", _HOST_NOUN)):
            for m in rx.finditer(back):
                if last is None or m.start() > last[1]:
                    last = (name, m.start())
                if _DISTINCT.search(back[max(0, m.start() - 14):m.start()]) and \
                        (last_distinct is None or m.start() > last_distinct[1]):
                    last_distinct = (name, m.start())
        chosen = last_distinct or last
        head = chosen[0] if chosen else None''',
    r'''    if head is None and not phrase.strip():
        # "...count distinct accounts that failed...; if the count reaches four or more" - object named earlier
        head = _object_from_context(back_wide or before[-160:], forward_wide or after[:120])''')

# ---------------------------------------------------------------------------------------------- windows
sub(r'''_APPROX = re.compile(r"(?:\babout|\broughly|\bapproximately|\baround|\bnearly|\balmost|\bsome|~|\bunder|\bjust over|\bwell under|\bwell over)\s*$")''',
    r'''_APPROX = re.compile(r"(?:\babout|\broughly|\bapproximately|\baround|\bnearly|\balmost|\bsome|~|\bjust\s+under|\bwell\s+under|\bjust\s+over|\bwell\s+over|\ba\s+little\s+under)\s*$")''')
sub(r'''r"(?:\b(within|inside|in|over|during|across|per|every|each|for|last|past|rolling|sliding|preceding|previous|prior|trailing)\s+"
    r"(?:(?:a|the|any|one|an)\s+)?(?:rolling\s+|sliding\s+|single\s+)?|"''',
    r'''r"(?:\b(within|inside|in|over|during|across|per|every|each|for|last|past|rolling|sliding|preceding|previous|prior|trailing|under|throughout|next)\s+"
    r"(?:(?:a|the|any|one|an)\s+)?(?:rolling\s+|sliding\s+|single\s+)?|"
    r"\b(less\s+than|no\s+more\s+than|not\s+more\s+than|inside\s+of)\s+(?:a\s+)?|"
    r"\b(?:time\s+)?(?:window|interval|period|timeframe|duration|span)\s*[|:=]\s*[|\s]*|"''')
sub(r'''    cue_m = _WINDOW_CUE.search(before)
        aft_m = _AFTER_WINDOW.match(after)''', r'''    cue_m = _WINDOW_CUE.search(before)
        aft_m = _AFTER_WINDOW.match(after)''') if False else None
# glued abbreviations: "2min", "90s", "3hrs"
sub(r'''_A_UNIT = re.compile(r"\b(?P<art>an?|half an?)\s+(?P<u>hour|minute|second|day)\b")''',
    r'''_TIME_GLUED = re.compile(r"(?<![\w.:])(?P<n>\d+(?:\.\d+)?)(?P<u>s|sec|secs|min|mins|h|hr|hrs)\b")
_A_UNIT = re.compile(r"\b(?P<art>an?|half an?)\s+(?P<u>hour|minute|second|day)\b")''')
sub(r'''    for m in _A_UNIT.finditer(low):
        s, e = m.span()''',
    r'''    for m in _TIME_GLUED.finditer(low):
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
        s, e = m.span()''')
sub(r'''        cue = (cue_m.group(1) or cue_m.group(2)) if cue_m else ("window" if aft_m else ("hyphenated" if hyphenated else None))''',
    r'''        cue = (cue_m.group(1) or cue_m.group(2) or cue_m.group(3) or "label") if cue_m else ("window" if aft_m else ("hyphenated" if hyphenated else None))''')
sub(r'''                                   cue=(cue_m.group(1) or cue_m.group(2)) if cue_m else "window",''',
    r'''                                   cue=(cue_m.group(1) or cue_m.group(2) or cue_m.group(3) or "label") if cue_m else "window",''')
sub(r'''_STRONG_CUES = {"within", "inside", "rolling", "sliding", "window", "period", "interval", "span", "timeframe",
                "preceding", "previous", "last", "past"}''',
    r'''_STRONG_CUES = {"within", "inside", "rolling", "sliding", "window", "period", "interval", "span", "timeframe",
                "preceding", "previous", "last", "past", "under", "label"}''')
# canonical_unit: bare "s" / "h"
sub(r'''    w = word.lower()
    if w.startswith("sec"):''', r'''    w = word.lower()
    if w == "s" or w.startswith("sec"):''')

# ---------------------------------------------------------------------------------------- rule sentences
sub(r'''_RULE_CUE = re.compile(''', r'''# "We explicitly do NOT want an alert when 5 or more accounts fail ...": a sentence that negates the intent to alert
# states no rule even though it contains a comparator. (Bare "no" is deliberately absent: "no fewer than 5".)
_NEGATED_INTENT = re.compile(
    r"\b(?:do(?:es)?\s+not|don't|doesn't|did\s+not|should\s+not|shouldn't|must\s+not|shall\s+not|never)\b[^.;\n]{0,30}"
    r"\b(?:want|need|wish|expect|require|alert|fire|trigger|flag|page|escalate)\b|\bexplicitly\s+not\b|\bno\s+alert\b")
_RULE_CUE = re.compile(''')
sub(r'''    hits = {(s, e) for s, e in sents if _RULE_CUE.search(low[s:e]) and (s, e) not in incidental
            and not (_NEGATED.search(low[s:e]) and (s, e) not in comparator_sents)}
    for c in counts:
        sent = sentence_of(sents, c.evidence.start)
        if c.comparator != "unspecified" and sent not in incidental:
            hits.add(sent)                                 # a comparator ("5 or more") is how rules are stated
    return sorted(hits)''',
    r'''    negated_intent = {(s, e) for s, e in sents if _NEGATED_INTENT.search(low[s:e])}
    hits = {(s, e) for s, e in sents if _RULE_CUE.search(low[s:e]) and (s, e) not in incidental
            and not (_NEGATED.search(low[s:e]) and (s, e) not in comparator_sents)}
    for c in counts:
        sent = sentence_of(sents, c.evidence.start)
        if c.comparator not in ("unspecified", "invalid") and sent not in incidental:
            hits.add(sent)                                 # a comparator ("5 or more") is how rules are stated
        if c.comparator == "invalid":
            hits.add(sent)                                 # kept as a rule sentence so the invalid number is REPORTED
    return sorted(hits - negated_intent)''')

# ---------------------------------------------------------------------------------------------- cues
sub(r'''    r"(?:then|and then|and subsequently|subsequently|afterwards?|followed by(?: a)?|and)\s+(?:then\s+)?{_SUBJ}"''' if False else r'''_THEN_SUCCESS = re.compile(''',
    r'''_THEN_SUCCESS = re.compile(
    r"(?:then|after(?:wards| that| this)?|later|next|followed)\b[^.;]{0,40}\bsucce(?:ss|ed|eds|ssful|ssfully)\b|"
    r"(?:ended|ending|concluded|culminat\w+|finish\w+)\s+(?:by|in|with)\s+(?:a\s+)?succe|then:\s*(?:a\s+)?succe|"''')
sub(r'''_ONE_HOST = re.compile(
    r"''', r'''_ONE_HOST = re.compile(
    r"\bgroup(?:ed)?\s+by\s*[:|]?\s*(?:the\s+)?(?:source[_ ]|originating\s+)?(?:host|machine)|"
    r"''')
sub(r'''_ONE_ACCOUNT = re.compile(
    r"''', r'''_ONE_ACCOUNT = re.compile(
    r"\bgroup(?:ed)?\s+by\s*[:|]?\s*(?:the\s+)?(?:user\s+)?(?:account|identity|user)|"
    r"''')
sub(r'''    r"mismatch|deviat|diverg|other than|rather than|instead of|not the (?:expected|approved|provisioned)|unexpected|not (?:equal|the same)")''',
    r'''    r"mismatch|deviat|diverg|other than|rather than|instead of|not the (?:expected|approved|provisioned)|unexpected|not (?:equal|the same)|"
    r"(?:is|are|was)(?: not|n't) the one|not (?:the )?same(?: as| like)?|inconsistent with|contradict\w*|conflicts? with|does not correspond|not what")''')
sub(r'''_EXPECTED_WORD = re.compile(r"\b(?:expected|approved|provisioned|assigned|registered|permitted|policy|identity export|defined)\b")''',
    r'''_EXPECTED_WORD = re.compile(r"\b(?:expected|approved|provisioned|assigned|registered|permitted|polic\w+|identity (?:export|system|store|provider|records?)|defined|"
                            r"lists?|listed|on record|recorded for|configured|supposed to|meant to|should use)\b")''')
sub(r'''    r"without (?:a |any |an )?(?:second factor|mfa|multi-?factor|2fa)|lack(?:s|ed|ing)?''',
    r'''    r"without (?:using |a |any |an |the )*(?:second factor|mfa|multi-?factor|2fa)|not use[sd]? (?:a |the )?(?:second factor|multi-?factor|mfa)|lack(?:s|ed|ing)?''')

# ---------------------------------------------------------------------------- bare field identifiers
sub(r'''    # 2. natural-language field lists announced by a strong cue: "the log has to provide X, Y and Z"''',
    r'''    # 1b. bare identifiers ("auth_method vs policy.expected_auth_method", "| mfa_used | false |")
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
    # 2. natural-language field lists announced by a strong cue: "the log has to provide X, Y and Z"''')
sub(r'''_CODE_TOKEN = re.compile(''', r'''_BARE_FIELD = re.compile(r"(?<![\w.`\"'])(policy\.[A-Za-z_]+|event_id|account_id|event_type|source_host|source_ip|auth_method|mfa_used|session_id|expected_auth_method|mfa_required)(?![\w])")
_CODE_TOKEN = re.compile(''')
p.write_text(s, encoding="utf-8")

# ------------------------------------------------------------------------------------- textutil.plain
t = REPO / "sentinelforge" / "textutil.py"
u = t.read_text(encoding="utf-8")
old = "def plain(text: str) -> str:"
assert old in u
u = u.replace(old, r'''_PAREN_DIGITS = re.compile(r"(?<=[a-z])\s?\(\d+\)")


def plain(text: str) -> str:''', 1)
old2 = "    assert len(out) == len(text)\n    return out"
assert old2 in u
u = u.replace(old2, r'''    # "ten (10)" / "thirty (30)": the parenthesised digits repeat the word; blank them (same length) so the word is parsed
    out = _PAREN_DIGITS.sub(lambda m: " " * len(m.group(0)), out)
    assert len(out) == len(text)
    return out''', 1)
t.write_text(u, encoding="utf-8")

# -------------------------------------------------------------------------------------------- reconcile
r = REPO / "sentinelforge" / "reconcile.py"
v = r.read_text(encoding="utf-8")
old3 = '''    rule = [c for c in rep.counts if c.ruleSentence]
    ok, wrong_sem, unknown, upper, unresolved = [], [], [], [], []
    for c in rule:
        if c.comparator == "lt":'''
assert old3 in v
v = v.replace(old3, '''    rule = [c for c in rep.counts if c.ruleSentence]
    ok, wrong_sem, unknown, upper, unresolved, invalid, unspecified = [], [], [], [], [], [], []
    for c in rule:
        if c.comparator == "invalid":
            invalid.append(c)
        elif c.comparator == "lt":''')
old4 = '''        elif c.comparator == "unspecified":
            continue
        elif c.unknownObject:'''
assert old4 in v
v = v.replace(old4, '''        elif c.comparator == "unspecified":
            unspecified.append(c)
        elif c.unknownObject:''')
old5 = '''    if upper:
        reasons.append(Reason("COUNT_UPPER_BOUND", "reject",'''
assert old5 in v
v = v.replace(old5, '''    if invalid:
        reasons.append(Reason("COUNT_INVALID_NUMBER", "reject",
                              "The passage gives a negative number where a minimum count belongs.", [c.evidence for c in invalid]))
    if upper:
        reasons.append(Reason("COUNT_UPPER_BOUND", "reject",''')
old6 = '''    if not ok:
        if not (upper or unknown or wrong_sem):'''
assert old6 in v
v = v.replace(old6, '''    if not ok:
        if unspecified and not (upper or unknown or wrong_sem or invalid):
            reasons.append(Reason("COUNT_COMPARATOR_UNSPECIFIED", "review",
                                  "A number is given for what to count but not how it is compared (at least? exactly?); nothing was assumed.",
                                  [c.evidence for c in unspecified]))
        elif not (upper or unknown or wrong_sem or invalid):''')
old7 = '''    if rule:
        return rule[0], False
    loose ='''
assert old7 in v
v = v.replace(old7, '''    if rule:
        # a second, UNCUED duration in a sentence that states the rule ("within 90 minutes or maybe 90 seconds",
        # "within 2 minutes (that is, 20 minutes)") is an unresolved alternative, never something to ignore
        chosen = rule[0].seconds
        alt = [w for w in rep.windows if w.ruleSentence and not w.approximate and not w.cue and w.seconds != chosen]
        if alt:
            reasons.append(Reason("WINDOW_UNRESOLVED_ALTERNATIVE", "review",
                                  "Another duration appears in the rule's own sentence without saying what it is "
                                  f"(\\"{alt[0].evidence.quote}\\" besides \\"{rule[0].evidence.quote}\\"); nothing was assumed.",
                                  [rule[0].evidence, alt[0].evidence]))
        return rule[0], False
    loose =''')
r.write_text(v, encoding="utf-8")
print("patched")
