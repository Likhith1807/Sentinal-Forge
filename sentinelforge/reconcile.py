"""Reconcile an extractor's *proposal* with the report's *evidence*.

Any extractor (regex, prompted LLM, fine-tuned transformer, or none at all) proposes a behaviour and
some numbers. `reconcile()` will only let a value into the compiled rule if it is backed by characters
in the report that the deterministic finder (`conditions.py`) located and parsed - and it refuses,
rather than picks, whenever the two disagree.

Three outcomes, deliberately distinct:

* ``accepted``      - every value has evidence and nothing contradicts it.
* ``rejected``      - the passage contains positive evidence the report is NOT expressible: a counted
                      object no recipe counts, a qualifier the recipe cannot honour, two different
                      thresholds, a field the schema does not have.
* ``needs_review``  - evidence is merely absent or two independent readings disagree; a person decides.

A classifier's confidence never appears in this function's decision: "a high classifier score alone
must not authorise compilation".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import behaviours as B
from .conditions import (ConditionReport, CountCandidate, Evidence, WindowCandidate, find_conditions,
                         verify_quote)

CONTRACT_VERSION = "2"
ACCEPTED, REJECTED, NEEDS_REVIEW = "accepted", "rejected", "needs_review"

_STRONG_WINDOW_CUES = {"within", "inside", "window", "preceding", "previous", "prior", "last", "past", "rolling",
                       "sliding", "hyphenated", "in", "over", "during", "under", "label", "abbreviated"}
_COUNT_KIND = {  # recipe -> the event type the counted thing must be
    "repeated-failed-login-then-success": "login_failure",
    "password-spray-across-accounts": "login_failure",
    "multi-host-authentication": "login_success",
}


@dataclass
class Reason:
    code: str
    severity: str                   # "reject" | "review"
    message: str
    evidence: list = field(default_factory=list)      # list[Evidence]

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message,
                "evidence": [e.to_dict() for e in self.evidence]}


@dataclass
class Reconciliation:
    status: str
    behaviourId: str | None
    spec: dict | None                                  # contract v2; present unless rejected outright
    reasons: list = field(default_factory=list)        # list[Reason]
    uncertainties: list = field(default_factory=list)  # residual limits shown next to any accepted rule
    proposal: dict = field(default_factory=dict)       # what the extractor claimed, verbatim, for audit
    conditions: dict = field(default_factory=dict)     # the full candidate set, for the UI to highlight

    @property
    def codes(self) -> list[str]:
        return [r.code for r in self.reasons]

    def to_dict(self) -> dict:
        return {"contractVersion": CONTRACT_VERSION, "status": self.status, "behaviourId": self.behaviourId,
                "spec": self.spec, "reasons": [r.to_dict() for r in self.reasons],
                "uncertainties": self.uncertainties, "proposal": self.proposal, "conditions": self.conditions}


# ------------------------------------------------------------------------------------- proposals

def normalize_proposal(extraction: dict | None) -> dict:
    """Bring any extractor's output to {behaviourId, threshold:{value,semantics|None,ambiguous,keys}, timeWindow}.

    Legacy threshold keys are mapped through the behaviour's alias table ONLY when the key is unambiguous;
    "loginCount" - which once meant "distinct hosts" for one behaviour and could equally mean an event
    count - is reported as ambiguous instead of guessed."""
    if not extraction:
        return {"behaviourId": None, "provided": False}
    bid = B.canonical_id(extraction.get("behaviourId"))
    beh = B.BEHAVIOURS.get(bid)
    thr = extraction.get("threshold")
    out: dict[str, Any] = {"behaviourId": bid, "provided": True,
                           "requiredFields": list(extraction.get("requiredFields") or []),
                           "policyFields": list(extraction.get("policyFields") or []),
                           "provenance": extraction.get("provenance") or {},
                           "timeWindow": extraction.get("timeWindow")}
    if isinstance(thr, dict) and thr:
        nums = {k: v for k, v in thr.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        out["threshold"] = {"raw": thr, "conflict": len(nums) > 1}
        if len(nums) == 1:
            key, val = next(iter(nums.items()))
            sem = beh.threshold_aliases.get(key) if beh else None
            out["threshold"].update({"value": val, "key": key, "semantics": sem, "ambiguousAlias": sem is None})
    return out


# ------------------------------------------------------------------------------------- selection

_REVISION = re.compile(r"\b(?:actual(?:ly)?|agreed|on further review|instead|revised|updated|corrected?|correction|supersed\w+|"
                       r"should (?:be|have been)|rather than|draft value|was a draft|no longer|not \d+)\b", re.IGNORECASE)


def _revisions(text: str, candidates, chosen_value) -> list:
    """Numbers stated in a sentence that revises / corrects something ("the actual threshold is 10, not 5"), whether or not
    that sentence looks like a rule. A correction that disagrees with the value we would compile is an unresolved conflict."""
    out = []
    for c in candidates:
        if c.value != chosen_value and _REVISION.search(text[c.evidence.sentenceStart:c.evidence.sentenceEnd]):
            out.append(c)
    return out


def _select_count(rep: ConditionReport, beh: B.Behaviour, reasons: list[Reason], text: str = "") -> CountCandidate | None:
    want_kind = _COUNT_KIND.get(beh.id)
    rule = [c for c in rep.counts if c.ruleSentence]
    ok, wrong_sem, unknown, upper, unresolved, invalid, unspecified = [], [], [], [], [], [], []
    for c in rule:
        if c.comparator == "invalid":
            invalid.append(c)
        elif c.comparator == "lt":
            upper.append(c)
        elif c.comparator == "unspecified":
            unspecified.append(c)
        elif c.unknownObject:
            unknown.append(c)
        elif c.semantics is None:
            unresolved.append(c)
        elif c.semantics != beh.count_semantics:
            wrong_sem.append(c)
        elif want_kind and c.eventKind not in (want_kind, None):
            wrong_sem.append(c)
        else:
            ok.append(c)

    if invalid:
        reasons.append(Reason("COUNT_INVALID_NUMBER", "reject",
                              "The passage gives a negative number where a minimum count belongs.", [c.evidence for c in invalid]))
    if upper:
        reasons.append(Reason("COUNT_UPPER_BOUND", "reject",
                              "The passage states an upper bound (fewer than / at most N); the rules only fire when a count reaches a minimum.",
                              [c.evidence for c in upper]))
    for c in unknown:
        reasons.append(Reason("COUNT_UNKNOWN_OBJECT", "reject",
                              f"The passage counts \"{c.unknownObject}\"; no supported behaviour counts that "
                              f"(supported: failed events, distinct accounts, distinct hosts).", [c.evidence]))
    for c in wrong_sem:
        reasons.append(Reason("COUNT_SEMANTICS_MISMATCH", "reject",
                              f"The passage counts {c.semantics or 'something else'}"
                              f"{' of ' + c.eventKind if c.eventKind else ''}, but {beh.display_name} counts "
                              f"{beh.count_semantics.replace('_', ' ')} of {_COUNT_KIND.get(beh.id, 'events')}.", [c.evidence]))

    values = {c.value for c in ok}
    if len(values) > 1:
        reasons.append(Reason("COUNT_CONFLICT", "reject",
                              f"The passage states different thresholds for the same condition ({sorted(values)}); "
                              f"a rule cannot honour both.", [c.evidence for c in ok]))
        return None
    if not ok:
        if unspecified and not (upper or unknown or wrong_sem or invalid):
            reasons.append(Reason("COUNT_COMPARATOR_UNSPECIFIED", "review",
                                  "A number is given for what to count but not how it is compared (at least? exactly?); nothing was assumed.",
                                  [c.evidence for c in unspecified]))
        elif not (upper or unknown or wrong_sem or invalid):
            reasons.append(Reason("COUNT_MISSING", "review",
                                  "No minimum count with a recognisable counted object was found in a sentence that states the rule.",
                                  [c.evidence for c in unresolved]))
        return None
    corrections = _revisions(text, [c for c in rep.counts if c.comparator in ("unspecified", "gte") and not c.unknownObject and c.semantics in (None, beh.count_semantics)], ok[0].value)
    if corrections:
        reasons.append(Reason("COUNT_REVISED_IN_TEXT", "review",
                              "The passage revises or corrects a number (\"" + corrections[0].evidence.quote + "\" in a sentence that "
                              "corrects something); it cannot be assumed which value is current.", [ok[0].evidence, corrections[0].evidence]))
    # an unresolved candidate with a different value is a second, unexplained number: a person should look
    stray = [c for c in unresolved if c.value not in values]
    if stray:
        reasons.append(Reason("COUNT_UNRESOLVED_NUMBER", "review",
                              "Another minimum-count expression with an unclear object states a different number.",
                              [c.evidence for c in stray]))
    return ok[0]


def _select_window(rep: ConditionReport, reasons: list[Reason], claimed: dict | None, text: str = "") -> tuple[WindowCandidate | None, bool]:
    """-> (window, found_outside_rule_sentence)"""
    rule = [w for w in rep.windows if w.ruleSentence and not w.approximate and w.cue]
    secs = {w.seconds for w in rule}
    if len(secs) > 1:
        reasons.append(Reason("WINDOW_CONFLICT", "reject",
                              "The rule sentences state different time windows "
                              f"({', '.join(sorted({f'{w.amount:g} {w.unit}' for w in rule}))}); a rule has exactly one window.",
                              [w.evidence for w in rule]))
        return None, False
    if rule:
        # a second, UNCUED duration in a sentence that states the rule ("within 90 minutes or maybe 90 seconds",
        # "within 2 minutes (that is, 20 minutes)") is an unresolved alternative, never something to ignore
        chosen = rule[0].seconds
        revised = [w for w in rep.windows if not w.approximate and w.seconds != chosen and _REVISION.search(text[w.evidence.sentenceStart:w.evidence.sentenceEnd])]
        if revised:
            reasons.append(Reason("WINDOW_REVISED_IN_TEXT", "review",
                                  "The passage revises or corrects a duration (\"" + revised[0].evidence.quote + "\"); it cannot be assumed which is current.",
                                  [rule[0].evidence, revised[0].evidence]))
        alt = [w for w in rep.windows if w.ruleSentence and not w.approximate and not w.cue and w.seconds != chosen]
        if alt:
            reasons.append(Reason("WINDOW_UNRESOLVED_ALTERNATIVE", "review",
                                  "Another duration appears in the rule's own sentence without saying what it is "
                                  f"(\"{alt[0].evidence.quote}\" besides \"{rule[0].evidence.quote}\"); nothing was assumed.",
                                  [rule[0].evidence, alt[0].evidence]))
        return rule[0], False
    loose = [w for w in rep.windows if not w.approximate and w.cue in _STRONG_WINDOW_CUES]
    if len({w.seconds for w in loose}) == 1:
        return loose[0], True
    if len({w.seconds for w in loose}) > 1:
        reasons.append(Reason("WINDOW_AMBIGUOUS", "review",
                              "Several different durations appear but none is inside a sentence that states the rule.",
                              [w.evidence for w in loose]))
        return None, False
    reasons.append(Reason("WINDOW_MISSING", "review", "No explicit time window (e.g. \"within 5 minutes\") was found.", []))
    return None, False


def _unit_word(claim: dict) -> tuple[float | None, str | None]:
    if not claim:
        return None, None
    amount, unit = claim.get("amount"), claim.get("unit")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None, unit
    return float(amount), unit


# ---------------------------------------------------------------------------------------- main

def reconcile(report_text: str, extraction: dict | None = None) -> Reconciliation:
    """Decide whether `extraction` (possibly None: evidence-only mode) may be compiled for `report_text`."""
    rep = find_conditions(report_text)
    proposal = normalize_proposal(extraction)
    reasons: list[Reason] = []
    uncertainties: list[str] = []

    def finish(status: str, bid: str | None, spec: dict | None) -> Reconciliation:
        return Reconciliation(status=status, behaviourId=bid, spec=spec, reasons=reasons,
                              uncertainties=uncertainties, proposal=proposal, conditions=rep.to_dict())

    # ---- 1. which behaviour? the model's claim must be corroborated by the passage itself
    complete = [s.behaviourId for s in rep.signals if s.complete]
    claimed = proposal.get("behaviourId")
    if claimed is not None and claimed not in B.BEHAVIOURS:
        reasons.append(Reason("UNKNOWN_BEHAVIOUR", "reject", f"\"{claimed}\" is not a supported behaviour.", []))
        return finish(REJECTED, None, None)

    # rejections that hold whichever behaviour is chosen
    hard_general: list[Reason] = []
    for q in rep.qualifiers:
        hard_general.append(Reason("UNSUPPORTED_QUALIFIER", "reject",
                                   f"The rule sentence adds a condition no supported recipe can evaluate: {q.kind}.", [q.evidence]))
    for f in rep.fields:
        if f.field is None and f.form == "code" and not f.incidental:
            hard_general.append(Reason("UNKNOWN_FIELD", "reject",
                                       f"The report requires \"{f.raw}\", which is not a field in the authentication log schema.",
                                       [f.evidence]))

    if claimed is None and proposal.get("provided"):
        # the extractor abstained
        if len(complete) == 1 and not hard_general:
            reasons.append(Reason("ABSTENTION_CONTRADICTED", "review",
                                  f"The extractor abstained but the passage contains every cue for {complete[0]}.", []))
            return finish(NEEDS_REVIEW, None, None)
        reasons.extend(hard_general)
        reasons.append(Reason("UNSUPPORTED_BEHAVIOUR", "reject",
                              "The extractor found no supported behaviour in this report and the passage contains no complete recipe.", []))
        return finish(REJECTED, None, None)

    if claimed is None:                                # evidence-only mode
        if len(complete) == 1:
            claimed = complete[0]
        elif len(complete) > 1:
            reasons.append(Reason("BEHAVIOUR_AMBIGUOUS", "review",
                                  f"The passage fits several behaviours ({', '.join(complete)}).", []))
            return finish(NEEDS_REVIEW, None, None)
        else:
            reasons.extend(hard_general)
            partial = [s for s in rep.signals if s.cues]
            if not hard_general and len(partial) == 1:
                p = partial[0]
                reasons.append(Reason("UNDERSPECIFIED_BEHAVIOUR", "review",
                                      f"The passage resembles {B.BEHAVIOURS[p.behaviourId].display_name} but does not state: "
                                      f"{'; '.join(p.missing)}. Nothing was guessed.", [c for c in p.cues[:2]]))
                return finish(NEEDS_REVIEW, None, None)
            reasons.append(Reason("UNSUPPORTED_BEHAVIOUR", "reject", "No supported recipe's conditions were found in the passage.", []))
            return finish(REJECTED, None, None)

    beh = B.BEHAVIOURS[claimed]
    reasons.extend(hard_general)

    sig = next(s for s in rep.signals if s.behaviourId == beh.id)
    if not sig.complete:
        other = [c for c in complete if c != beh.id]
        if other:
            reasons.append(Reason("BEHAVIOUR_CONFLICT", "review",
                                  f"The extractor chose {beh.id}, but the passage matches {', '.join(other)} and lacks: "
                                  f"{'; '.join(sig.missing)}.", []))
        elif not hard_general:
            reasons.append(Reason("RECIPE_CONDITIONS_NOT_FOUND", "reject" if _has_positive_incompatibility(rep) else "review",
                                  f"The passage does not contain what {beh.display_name} requires: {'; '.join(sig.missing)}.", []))
    elif len(complete) > 1:
        reasons.append(Reason("BEHAVIOUR_AMBIGUOUS", "review",
                              f"The passage also fits {', '.join(c for c in complete if c != beh.id)}.", []))

    # ---- 2. count
    count = window = None
    outside_rule_sentence = False
    if beh.windowed:
        count = _select_count(rep, beh, reasons, report_text)
        claim_thr = proposal.get("threshold")
        if count is not None and claim_thr:
            if claim_thr.get("conflict"):
                reasons.append(Reason("CONFLICTING_COUNT_KEYS", "reject",
                                      f"The extraction supplies several threshold values ({sorted(claim_thr['raw'])}).", []))
            elif claim_thr.get("ambiguousAlias"):
                reasons.append(Reason("AMBIGUOUS_COUNT_ALIAS", "review",
                                      f"The extraction labels its threshold \"{claim_thr.get('key')}\", which does not identify what is "
                                      f"counted; the passage reads as {count.semantics.replace('_', ' ')}.", [count.evidence]))
            elif claim_thr.get("value") != count.value:
                reasons.append(Reason("COUNT_MODEL_DISAGREES", "review",
                                      f"The extractor read the threshold as {claim_thr['value']}; the passage says {count.value} "
                                      f"(\"{count.evidence.quote}\").", [count.evidence]))
        window, outside_rule_sentence = _select_window(rep, reasons, proposal.get("timeWindow"), report_text)
        claim_w = proposal.get("timeWindow")
        if window is not None and claim_w:
            amt, unit = _unit_word(claim_w)
            claimed_seconds = None
            from .conditions import UNIT_SECONDS
            if amt is not None and unit in UNIT_SECONDS:
                claimed_seconds = amt * UNIT_SECONDS[unit]
            if claimed_seconds is None:
                reasons.append(Reason("WINDOW_MODEL_MALFORMED", "review", "The extractor's time window is not a number with a valid unit.", [window.evidence]))
            elif claimed_seconds != window.seconds:
                reasons.append(Reason("WINDOW_MODEL_DISAGREES", "review",
                                      f"The extractor read the window as {amt:g} {unit} ({claimed_seconds:g} s); the passage says "
                                      f"\"{window.evidence.quote}\" ({window.seconds:g} s).", [window.evidence]))
        elif window is not None and claim_w is None and proposal.get("provided"):
            uncertainties.append("The extractor supplied no time window; the value comes from the passage alone.")
        if outside_rule_sentence and window is not None:
            agree = False
            prov = (proposal.get("provenance") or {}).get("timeWindow") or {}
            if prov and window.evidence.start <= prov.get("charStart", -1) < window.evidence.end + 1:
                agree = True
            if not agree:
                reasons.append(Reason("WINDOW_OUTSIDE_RULE_SENTENCE", "review",
                                      "The only window found is not in the sentence that states the count; confirm it belongs to the rule.",
                                      [window.evidence]))
            else:
                uncertainties.append("The window sits outside the sentence stating the count; the extractor's own span agrees with it.")
    else:
        # policy recipes carry no numeric threshold; a stray one in the extraction is reported, never compiled
        if proposal.get("threshold") or proposal.get("timeWindow"):
            uncertainties.append("The extractor supplied a threshold/window for a behaviour that has none; it was ignored.")
        if beh.id == "auth-method-policy-violation" and rep.methodLiterals:
            reasons.append(Reason("DIRECTION_SPECIFIC_METHOD", "review",
                                  "The rule sentence names a specific authentication method; this behaviour fires on ANY mismatch "
                                  "with the expected method, so a direction-specific policy would over-alert.",
                                  [q.evidence for q in rep.methodLiterals[:2]]))

    # ---- 3. fields the report *asks the log to provide* vs what the recipe reads
    for f in rep.fields:
        if f.field is None and f.form == "phrase" and not f.incidental:
            reasons.append(Reason("UNMAPPED_FIELD_PHRASE", "review",
                                  f"The report asks for \"{f.raw}\", which could not be mapped to any log field.", [f.evidence]))
    needed = set(beh.log_fields) | set(beh.policy_fields)
    mentioned = {f.field for f in rep.fields if f.field and not f.incidental}
    extra = sorted(mentioned - needed - {"event_id"})
    if extra:
        uncertainties.append(f"The report lists {', '.join(extra)}, which the compiled rule does not read.")
    if proposal.get("provided"):
        claimed_fields = set(proposal.get("requiredFields", [])) | set(proposal.get("policyFields", []))
        if claimed_fields and not claimed_fields <= needed | {"event_id"}:
            uncertainties.append("The extractor listed fields beyond what the recipe reads: "
                                 f"{', '.join(sorted(claimed_fields - needed))}.")

    for c in rep.caveats[:1]:
        uncertainties.append(f"The report speaks of overlap/concurrency (\"{c.evidence.quote}\"); "
                             f"{beh.display_name} does not measure it. " + " ".join(beh.limitations[:1]))
    uncertainties.extend(beh.limitations)

    # ---- 4. assemble the evidence-backed spec (values come from the passage, never from a score)
    spec = _build_spec(beh, count, window, rep)
    if any(r.severity == "reject" for r in reasons):
        return finish(REJECTED, beh.id, spec)
    if any(r.severity == "review" for r in reasons):
        return finish(NEEDS_REVIEW, beh.id, spec)
    return finish(ACCEPTED, beh.id, spec)


def _has_positive_incompatibility(rep: ConditionReport) -> bool:
    return any(c.unknownObject or c.comparator == "lt" for c in rep.counts if c.ruleSentence)


def _build_spec(beh: B.Behaviour, count: CountCandidate | None, window: WindowCandidate | None, rep: ConditionReport) -> dict:
    spec: dict[str, Any] = {
        "contractVersion": CONTRACT_VERSION,
        "behaviourId": beh.id,
        "requiredFields": [f for f in beh.log_fields],
        "policyFields": list(beh.policy_fields),
        "conditions": {},
    }
    if count is not None:
        spec["conditions"]["count"] = {"value": count.value, "comparator": "gte", "semantics": beh.count_semantics,
                                       "eventKind": count.eventKind or _COUNT_KIND.get(beh.id),
                                       "form": count.form, "evidence": count.evidence.to_dict()}
        key = next((k for k, sem in beh.threshold_aliases.items() if sem == beh.count_semantics), None)
        spec["threshold"] = {key: count.value}
    if window is not None:
        spec["conditions"]["window"] = {"amount": window.amount if not float(window.amount).is_integer() else int(window.amount),
                                        "unit": window.unit, "seconds": window.seconds,
                                        "cue": window.cue, "evidence": window.evidence.to_dict()}
        spec["timeWindow"] = {"amount": spec["conditions"]["window"]["amount"], "unit": window.unit}
    return spec


def verify_spec_evidence(report_text: str, spec: dict) -> list[str]:
    """Quote verification of every evidence record in a spec against the report text (not a semantic check)."""
    bad = []
    for name, cond in (spec.get("conditions") or {}).items():
        ev = cond.get("evidence")
        if not ev or not verify_quote(report_text, ev):
            bad.append(name)
    return bad
