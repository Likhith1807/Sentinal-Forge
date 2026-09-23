"""Permanent regression tests for compiler/src/observability_checker.py.

Every case here is a real bug an independent external review found and
reproduced (see docs/spec/independent-review-corrections.md) — round 1
(empty spec, unknown behaviourId, negative/zero threshold) and round 2
(structural dependency check, fractional threshold, malformed timeWindow).
Before this file existed, each was verified once via an ad-hoc script and
reported, then never checked again — this is that verification made
permanent and re-runnable, which is the gap that left round 1's own fix
free to introduce round 2's KeyError bug undetected.

Usage:
    python compiler/test/test_observability_checker.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "compiler" / "src"))
sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))

import observability_checker as stage3  # noqa: E402

VALID_B1_SPEC = {
    "behaviourId": "repeated-failed-login-then-success",
    "requiredFields": ["account_id", "event_type", "timestamp"],
    "policyFields": [],
    "threshold": {"failureCount": 5},
    "timeWindow": {"amount": 2, "unit": "minutes"},
}


def case_valid_spec_supported():
    r = stage3.validate(VALID_B1_SPEC)
    assert r.status == "supported", r.notes


def case_empty_spec_rejected():
    """Round 1 finding: validate({}) returned 'supported'."""
    r = stage3.validate({})
    assert r.status == "rejected", "empty spec must be rejected"


def case_unknown_behaviour_id_rejected():
    """Round 1 finding: an arbitrary behaviourId not using the
    'unrecognized:' string-prefix convention passed straight through."""
    spec = {"behaviourId": "totally-made-up-behaviour", "requiredFields": ["account_id"], "policyFields": []}
    r = stage3.validate(spec)
    assert r.status == "rejected", "unknown behaviourId must be rejected regardless of naming convention"


def case_negative_threshold_rejected():
    """Round 1 finding: negative threshold/window values passed."""
    spec = dict(VALID_B1_SPEC, threshold={"failureCount": -5}, timeWindow={"amount": -2, "unit": "minutes"})
    r = stage3.validate(spec)
    assert r.status == "rejected"
    assert "threshold" in r.invalidValues and "timeWindow" in r.invalidValues


def case_zero_fields_rejected():
    spec = {"behaviourId": "repeated-failed-login-then-success", "requiredFields": [], "policyFields": []}
    r = stage3.validate(spec)
    assert r.status == "rejected", "a spec naming zero fields must be rejected"


def case_unreliable_field_still_rejected():
    """Confirms the round-2 rewrite didn't regress the original,
    already-correct source_ip check."""
    spec = {"behaviourId": "repeated-failed-login-then-success",
            "requiredFields": ["account_id", "event_type", "source_ip"], "policyFields": []}
    r = stage3.validate(spec)
    assert r.status == "rejected"
    assert "source_ip" in r.unreliableFields


def case_structural_dependency_missing_rejected():
    """Round 2 finding: a spec naming only 'timestamp' for repeated-failed-login-then-success (never
    requesting account_id, which that recipe's groupingKey structurally requires) validated as 'supported'
    even when account_id was unavailable. The recipe's dependencies are checked against the DATA whether or
    not the spec listed them."""
    spec = dict(VALID_B1_SPEC, requiredFields=["timestamp"])
    r = stage3.validate(spec, unavailable_fields={"account_id"})
    assert r.status == "rejected", "recipe's structural dependencies must be checked even when never requested"
    assert any("account_id" in n for n in r.notes)


def case_incomplete_field_list_is_not_itself_a_reason_to_reject():
    """AUDIT REGRESSION (2026-09): 8 of 40 supported reports were REFUSED because the extractor's field
    list omitted something the recipe reads anyway (e.g. `source_host`). The rule would have read the
    field correctly, and the data has it - the gap was in a model's list, not in the data. Rejection must
    follow the data, not the list."""
    spec = dict(VALID_B1_SPEC, requiredFields=["timestamp"])
    r = stage3.validate(spec)
    assert r.status == "supported", r.notes


def case_fractional_threshold_rejected():
    """Round 2 finding: threshold 0.5 was accepted (then silently
    truncated to 0 by spec_bridge.py)."""
    spec = dict(VALID_B1_SPEC, threshold={"failureCount": 0.5})
    r = stage3.validate(spec)
    assert r.status == "rejected", "a fractional count of events must be rejected, not truncated"


def case_missing_time_window_amount_does_not_crash():
    """Round 2 finding: a timeWindow dict missing 'amount' crashed
    observability_checker.py's own round-1 fix with KeyError."""
    spec = dict(VALID_B1_SPEC, timeWindow={"unit": "minutes"})
    r = stage3.validate(spec)  # must not raise
    assert r.status == "rejected"


def case_non_numeric_time_window_amount_does_not_crash():
    spec = dict(VALID_B1_SPEC, timeWindow={"amount": "two", "unit": "minutes"})
    r = stage3.validate(spec)  # must not raise
    assert r.status == "rejected"


def case_simulated_unavailable_field_rejects_even_if_requested():
    """The dashboard's field-unavailability simulation, corrected in
    round 1: the spec still asks for the field; Stage 3 must reject
    because it isn't AVAILABLE, not because it wasn't requested."""
    r = stage3.validate(VALID_B1_SPEC, unavailable_fields={"account_id", "event_type", "timestamp"})
    assert r.status == "rejected"
    assert set(r.missingFields) >= {"account_id", "event_type", "timestamp"}


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_")]


def main() -> None:
    failures = []
    for case in CASES:
        try:
            case()
            print(f"OK   {case.__name__}")
        except AssertionError as e:
            failures.append(case.__name__)
            print(f"FAIL {case.__name__}: {e}")

    if failures:
        raise SystemExit(f"\n{len(failures)}/{len(CASES)} case(s) failed: {failures}")
    print(f"\nAll {len(CASES)} cases passed.")


if __name__ == "__main__":
    main()
