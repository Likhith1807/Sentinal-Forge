"""Permanent regression tests for compiler/src/spec_bridge.py.

Every numeric case here is a real bug an independent external review
found and reproduced (round 2 — see
docs/spec/independent-review-corrections.md): a threshold silently
truncated instead of rejected, a window converted with the wrong
operation order, and — in observability_checker.py's own fix for the
first two — a KeyError crash on a malformed window, covered by the sibling
file test_observability_checker.py.

Usage:
    python compiler/test/test_spec_bridge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "compiler" / "src"))

import spec_bridge  # noqa: E402

VALID_B1_EXTRACTION = {
    "behaviourId": "repeated-failed-login-then-success",
    "requiredFields": ["account_id", "event_type", "timestamp"],
    "policyFields": [],
    "threshold": {"failureCount": 5},
    "timeWindow": {"amount": 2, "unit": "minutes"},
}


def case_valid_spec_builds():
    c = spec_bridge.build_compiled_spec(VALID_B1_EXTRACTION)
    assert c["recipe"] == "SequenceThenTrigger"
    assert c["countThreshold"] == 5
    assert c["timeWindowSeconds"] == 120


def case_fractional_threshold_raises():
    """Round 2 finding: int(0.5) == 0 — a fractional count silently
    became a meaningless threshold of zero instead of being rejected."""
    spec = dict(VALID_B1_EXTRACTION, threshold={"failureCount": 0.5})
    try:
        spec_bridge.build_compiled_spec(spec)
        raise AssertionError("expected UnbuildableSpecError for a fractional threshold")
    except spec_bridge.UnbuildableSpecError:
        pass


def case_fractional_window_converts_correctly():
    """Round 2 finding: int(1.5) * 60 == 60, not 90 — the amount was
    truncated BEFORE multiplying, losing the fractional 30 seconds."""
    spec = dict(VALID_B1_EXTRACTION, timeWindow={"amount": 1.5, "unit": "minutes"})
    c = spec_bridge.build_compiled_spec(spec)
    assert c["timeWindowSeconds"] == 90, f"expected 90, got {c['timeWindowSeconds']}"


def case_missing_amount_key_raises_not_crashes():
    """Round 2 finding: the checker's OWN fix crashed with KeyError on a
    missing 'amount' key. spec_bridge.py's own handling was already
    correct (this test pins that down permanently); the sibling crash was
    in observability_checker.py, covered separately."""
    spec = dict(VALID_B1_EXTRACTION, timeWindow={"unit": "minutes"})
    try:
        spec_bridge.build_compiled_spec(spec)
        raise AssertionError("expected UnbuildableSpecError for a timeWindow missing 'amount'")
    except spec_bridge.UnbuildableSpecError:
        pass


def case_threshold_under_wrong_behaviours_key_name_rejected():
    """2026-09-17 correctness-standard finding: the bridge used to accept
    ANY numeric value in the threshold dict regardless of its key name, so
    a value mislabelled under a different behaviour's key (e.g.
    "distinctAccountCount" landing on repeated-failed-login-then-success,
    a SequenceThenTrigger behaviour that has nothing to do with distinct
    accounts) would silently compile as if it meant this behaviour's
    count."""
    spec = dict(VALID_B1_EXTRACTION, threshold={"distinctAccountCount": 5})
    try:
        spec_bridge.build_compiled_spec(spec)
        raise AssertionError("expected UnbuildableSpecError for a threshold under the wrong behaviour's key name")
    except spec_bridge.UnbuildableSpecError:
        pass


def case_threshold_accepts_real_transformer_synonym():
    """A live run of nlp/src/transformer_extractor.py on the real
    concurrent-sessions-003 held-out report (2026-09-17) returned
    {"loginCount": 2} — NOT gold's {"successCount": 2} — a reasonable
    synonym, not a mislabelling. The threshold-key check must accept this
    real extractor output, not just the gold fixture's own spelling."""
    spec = {
        "behaviourId": "concurrent-sessions-different-hosts",
        "requiredFields": ["account_id", "event_type", "timestamp", "source_host"],
        "policyFields": [],
        "threshold": {"loginCount": 2},
        "timeWindow": {"amount": 15, "unit": "minutes"},
    }
    c = spec_bridge.build_compiled_spec(spec)
    assert c["distinctThreshold"] == 2


def case_unknown_behaviour_id_raises():
    spec = dict(VALID_B1_EXTRACTION, behaviourId="not-a-real-behaviour")
    try:
        spec_bridge.build_compiled_spec(spec)
        raise AssertionError("expected UnbuildableSpecError for an unknown behaviourId")
    except spec_bridge.UnbuildableSpecError:
        pass


def case_policy_compare_needs_no_numbers():
    spec = {"behaviourId": "mfa-bypass-on-required-account", "requiredFields": ["account_id", "event_type", "mfa_used"],
            "policyFields": ["policy.mfa_required"], "threshold": None, "timeWindow": None}
    c = spec_bridge.build_compiled_spec(spec)
    assert c["recipe"] == "PolicyCompare"
    assert c["comparisonOp"] == "falseWhenRequired"


def case_structural_dependencies_known_behaviour():
    log_fields, policy_fields = spec_bridge.structural_dependencies("repeated-failed-login-then-success")
    assert log_fields == {"account_id", "event_type", "timestamp"}
    assert policy_fields == set()


def case_structural_dependencies_policy_compare():
    log_fields, policy_fields = spec_bridge.structural_dependencies("mfa-bypass-on-required-account")
    assert {"account_id", "event_type", "mfa_used"} <= log_fields
    assert policy_fields == {"policy.mfa_required"}


def case_structural_dependencies_unknown_behaviour_returns_empty():
    log_fields, policy_fields = spec_bridge.structural_dependencies("not-a-real-behaviour")
    assert log_fields == set() and policy_fields == set()


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
