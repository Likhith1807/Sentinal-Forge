"""Classical (rule-based, no learning) extractor.

Deliberately not a statistical model — this is pattern-matching over the
report's semi-structured "Analyst-confirmed detection parameters" section,
the kind of thing a regex-and-keyword system could have done in 2015. It
exists as the cheap, fast baseline Phase 2's plan called for: something
that gets a real number quickly, against which the transformer-based
extractor's improvement (or lack of one) can be measured honestly.

It exploits document structure on purpose: every SENTINEL Forge sample
report ends with an "Analyst-confirmed detection parameters" section where
thresholds and field names are stated in a semi-structured way. Restricting
most cues to that section (rather than the free-form narrative) is a
realistic classical-NLP trick — and it is also exactly where this baseline
is expected to break: a report whose confirmed parameters are phrased
differently, or split across sections, will fool it. That fragility is the
point of having it as a baseline, not a flaw to hide.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from schema_fields import LOG_FIELDS, POLICY_FIELDS

NEGATION_CUES = [
    "not part of", "isn't part of", "is not part of",
    "not a requirement", "not required", "incidental",
]

BEHAVIOUR_KEYWORDS = {
    "password-spray-across-accounts": ["distinct account", "spray", "distinct-account"],
    "concurrent-sessions-different-hosts": ["different host", "two hosts", "unrelated host", "simultaneous", "concurrent"],
    "service-account-interactive-auth": ["expected_auth_method", "provisioned for", "provisioned to", "certificate-only", "auth_method` !=", "does not match", "mismatch direction"],
    "mfa-bypass-on-required-account": ["mfa_required", "mfa-required", "without mfa", "mfa bypass", "without a challenge", "without challenge"],
    "repeated-failed-login-then-success": ["failed sign-in", "failed authentication", "failed logon", "failed login", "consecutive failed", "back-to-back bad password", "bad-password"],
}


@dataclass
class ExtractionResult:
    behaviourId: str
    requiredFields: list = field(default_factory=list)
    policyFields: list = field(default_factory=list)
    excludedFields: list = field(default_factory=list)
    threshold: dict | None = None
    timeWindow: dict | None = None
    # field/behaviourId name -> {"charStart", "charEnd", "text"} into the
    # ORIGINAL report_text passed to extract(). Every regex match's span is
    # a real offset, never invented — this is what lets Stage 2's output
    # satisfy the evidence-traceability requirement in
    # docs/spec/behaviour-ir-format.md.
    provenance: dict = field(default_factory=dict)


@dataclass
class _Section:
    text: str
    offset: int  # absolute start of `text` within the original report_text


def split_sections(report_text: str) -> tuple[_Section, _Section]:
    """Returns (narrative, params) sections, each carrying its absolute
    offset into report_text so downstream regex spans can be translated
    back to the original document rather than the sliced substring."""
    narrative_match = re.search(r"## Narrative\s*\n(.*?)(?=\n## |\Z)", report_text, re.S)
    params_match = re.search(r"## Analyst-confirmed detection parameters\s*\n(.*?)(?=\n## |\Z)", report_text, re.S)
    narrative = _Section(narrative_match.group(1), narrative_match.start(1)) if narrative_match else _Section("", 0)
    params = _Section(params_match.group(1), params_match.start(1)) if params_match else _Section("", 0)
    return narrative, params


def classify_behaviour(full_text: str) -> str:
    text = full_text.lower()
    scores = {
        behaviour_id: sum(text.count(kw.lower()) for kw in keywords)
        for behaviour_id, keywords in BEHAVIOUR_KEYWORDS.items()
    }
    # repeated-failed-login-then-success is the fallback default: only
    # picked on a real keyword hit, not merely because nothing else matched.
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "repeated-failed-login-then-success"


def is_negated(section_text: str, match_end: int, window: int = 100) -> bool:
    snippet = section_text[match_end:match_end + window].lower()
    return any(cue in snippet for cue in NEGATION_CUES)


def _span(section: _Section, match: re.Match, report_text: str) -> dict:
    start = section.offset + match.start()
    end = section.offset + match.end()
    return {"charStart": start, "charEnd": end, "text": report_text[start:end]}


def extract(report_text: str) -> ExtractionResult:
    narrative, params = split_sections(report_text)
    full_text = narrative.text + "\n" + params.text

    behaviour_id = classify_behaviour(full_text)
    required: list[str] = []
    policy: list[str] = []
    excluded: list[str] = []
    provenance: dict = {}

    # behaviour classification provenance: first matching keyword, wherever
    # it actually occurs (narrative or params)
    for keyword in BEHAVIOUR_KEYWORDS.get(behaviour_id, []):
        for section in (narrative, params):
            m = re.search(re.escape(keyword), section.text, re.I)
            if m:
                provenance["behaviourId"] = _span(section, m, report_text)
                break
        if "behaviourId" in provenance:
            break

    for section in (narrative, params):
        m = re.search(r"\baccounts?\b", section.text, re.I)
        if m and "account_id" not in required:
            required.append("account_id")
            provenance["account_id"] = _span(section, m, report_text)
        m = re.search(r"\b(login|logon|sign-?in|authenticat\w*)\b", section.text, re.I)
        if m and "event_type" not in required:
            required.append("event_type")
            provenance["event_type"] = _span(section, m, report_text)

    m = re.search(r"\b(window|minutes?|seconds?)\b", params.text, re.I)
    if m:
        required.append("timestamp")
        provenance["timestamp"] = _span(params, m, report_text)

    m = re.search(r"\bhosts?\b", params.text, re.I)
    if m:
        required.append("source_host")
        provenance["source_host"] = _span(params, m, report_text)

    for literal in ["auth_method", "mfa_used"]:
        m = re.search(re.escape(literal), params.text)
        if m:
            span = _span(params, m, report_text)
            if is_negated(params.text, m.end()):
                excluded.append(literal)
                provenance[f"excluded:{literal}"] = span
            else:
                required.append(literal)
                provenance[literal] = span

    for literal, policy_name in [
        ("expected_auth_method", "policy.expected_auth_method"),
        ("mfa_required", "policy.mfa_required"),
    ]:
        m = re.search(re.escape(literal), params.text, re.I)
        if m:
            policy.append(policy_name)
            provenance[policy_name] = _span(params, m, report_text)

    threshold = None
    count_match = re.search(r"(?:threshold:\s*)?(\d+)\s*(?:or more)?\s*(?:failed|failures|distinct)", params.text, re.I)
    if count_match:
        n = int(count_match.group(1))
        if "distinct" in params.text.lower():
            threshold = {"distinctAccountCount": n}
        elif "success" in behaviour_id or "concurrent" in behaviour_id:
            threshold = {"successCount": n}
        else:
            threshold = {"failureCount": n}
        provenance["threshold"] = _span(params, count_match, report_text)

    time_window = None
    window_match = re.search(r"(\d+)\s*minutes?", params.text, re.I)
    if window_match:
        time_window = {"amount": int(window_match.group(1)), "unit": "minutes"}
        provenance["timeWindow"] = _span(params, window_match, report_text)

    # keep only fields in the controlled vocabulary — defends against a
    # regex accidentally matching something outside the schema
    required = [f for f in dict.fromkeys(required) if f in LOG_FIELDS]
    policy = [f for f in dict.fromkeys(policy) if f in POLICY_FIELDS]

    return ExtractionResult(
        behaviourId=behaviour_id,
        requiredFields=required,
        policyFields=policy,
        provenance=provenance,
        excludedFields=excluded,
        threshold=threshold,
        timeWindow=time_window,
    )
