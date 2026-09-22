"""Offline tests for hybrid_extractor and consistency_extractor's aggregation logic.

Neither the fine-tuned model nor the Groq API is required: finetuned_extractor.extract and
transformer_extractor.extract are monkeypatched with deterministic fakes, so these tests check the
WIRING (fallback trigger condition, majority-vote aggregation) — not model quality, and not live
API behaviour, which is covered separately (evaluate_corpus.py, consistency_extractor.measure_variance)
and is subject to the Groq daily quota documented in docs/corpus.md.

Run directly: `python nlp/test/test_hybrid_and_consistency.py`
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))

import hybrid_extractor  # noqa: E402
import consistency_extractor  # noqa: E402
import finetuned_extractor  # noqa: E402
import transformer_extractor  # noqa: E402


def _fake_finetuned(behaviour_id, confidence, **kw):
    def fn(text, model_dir=None):
        return SimpleNamespace(behaviourId=behaviour_id, requiredFields=kw.get("requiredFields", []),
                               policyFields=kw.get("policyFields", []), threshold=kw.get("threshold"),
                               timeWindow=kw.get("timeWindow"), provenance={},
                               raw={"behaviourConfidence": confidence})
    return fn


def _fake_prompted(behaviour_id, **kw):
    def fn(text, model=None):
        return {"behaviourId": behaviour_id, "requiredFields": kw.get("requiredFields", []),
                "policyFields": kw.get("policyFields", []), "threshold": kw.get("threshold"),
                "timeWindow": kw.get("timeWindow"), "provenance": {}}
    return fn


def test_hybrid_uses_fine_tuned_directly_when_confident():
    finetuned_extractor.extract = _fake_finetuned("repeated-failed-login-then-success", 0.95,
                                                  requiredFields=["account_id"])
    transformer_extractor.extract = _fake_prompted("SHOULD_NOT_BE_CALLED")
    result = hybrid_extractor.extract("some report text")
    assert result.source == "fine-tuned"
    assert result.behaviourId == "repeated-failed-login-then-success"
    assert result.requiredFields == ["account_id"]


def test_hybrid_falls_back_when_fine_tuned_predicts_unsupported():
    finetuned_extractor.extract = _fake_finetuned(None, 0.9)
    transformer_extractor.extract = _fake_prompted("password-spray-across-accounts", requiredFields=["source_host"])
    result = hybrid_extractor.extract("some report text")
    assert result.source == "prompted-fallback"
    assert result.behaviourId == "password-spray-across-accounts"
    assert result.requiredFields == ["source_host"]


def test_hybrid_falls_back_when_fine_tuned_confidence_is_low():
    finetuned_extractor.extract = _fake_finetuned("mfa-bypass-on-required-account", 0.4)
    transformer_extractor.extract = _fake_prompted("mfa-bypass-on-required-account", requiredFields=["mfa_used"])
    result = hybrid_extractor.extract("some report text", confidence_threshold=0.6)
    assert result.source == "prompted-fallback" and result.fineTunedConfidence == 0.4


def test_hybrid_respects_a_custom_confidence_threshold():
    finetuned_extractor.extract = _fake_finetuned("mfa-bypass-on-required-account", 0.55)
    transformer_extractor.extract = _fake_prompted("SHOULD_NOT_BE_CALLED")
    assert hybrid_extractor.extract("x", confidence_threshold=0.5).source == "fine-tuned"
    transformer_extractor.extract = _fake_prompted("mfa-bypass-on-required-account")
    assert hybrid_extractor.extract("x", confidence_threshold=0.6).source == "prompted-fallback"


def test_hybrid_treats_unrecognized_fallback_behaviour_as_abstention():
    finetuned_extractor.extract = _fake_finetuned(None, 0.9)
    transformer_extractor.extract = _fake_prompted("unrecognized:something-the-model-invented")
    result = hybrid_extractor.extract("some report text")
    assert result.behaviourId is None


def test_consistency_majority_vote_on_behaviour_and_fields():
    calls = iter([
        {"behaviourId": "repeated-failed-login-then-success", "requiredFields": ["account_id", "event_type"],
         "policyFields": [], "threshold": {"failureCount": 5}, "timeWindow": {"amount": 2, "unit": "minutes"}, "provenance": {}},
        {"behaviourId": "repeated-failed-login-then-success", "requiredFields": ["account_id", "timestamp"],
         "policyFields": [], "threshold": {"failureCount": 5}, "timeWindow": {"amount": 2, "unit": "minutes"}, "provenance": {}},
        {"behaviourId": "concurrent-sessions-different-hosts", "requiredFields": ["account_id"],
         "policyFields": [], "threshold": None, "timeWindow": None, "provenance": {}},
    ])
    transformer_extractor.extract = lambda text, model=None: next(calls)
    result = consistency_extractor.extract("some report text", n_samples=3)
    assert result["behaviourId"] == "repeated-failed-login-then-success"   # 2 of 3
    assert result["requiredFields"] == ["account_id"]                     # only field in >=2 of 3
    assert result["threshold"] == {"failureCount": 5}                     # majority (2 of 3 non-null and equal)
    assert abs(result["agreement"]["behaviourId"] - 2 / 3) < 1e-9


def test_consistency_excludes_a_failing_sample_from_the_vote_instead_of_crashing():
    good = {"behaviourId": "repeated-failed-login-then-success", "requiredFields": ["account_id"],
            "policyFields": [], "threshold": {"failureCount": 5}, "timeWindow": None, "provenance": {}}
    calls = iter([good, good])

    def flaky(text, model=None):
        if next(flaky.count) == 0:
            raise ValueError("Model output was not parseable JSON: ''")
        return next(calls)
    flaky.count = iter([0, 1, 1])
    transformer_extractor.extract = flaky
    result = consistency_extractor.extract("some report text", n_samples=3)
    assert result["nFailed"] == 1 and result["behaviourId"] == "repeated-failed-login-then-success"
    assert result["agreement"]["behaviourId"] == 1.0    # both SUCCESSFUL samples agreed; the failure isn't a vote


def test_consistency_handles_every_sample_failing():
    transformer_extractor.extract = lambda text, model=None: (_ for _ in ()).throw(ValueError("boom"))
    result = consistency_extractor.extract("x", n_samples=3)
    assert result["behaviourId"] is None and result["nFailed"] == 3 and result["samples"] == []


def test_measure_variance_skips_reports_with_fewer_than_two_successful_samples():
    good = {"behaviourId": "b1", "requiredFields": [], "policyFields": [], "threshold": None, "timeWindow": None}
    sequence = iter([good, ValueError("boom"), ValueError("boom")])

    def flaky(text, model=None):
        item = next(sequence)
        if isinstance(item, Exception):
            raise item
        return item
    transformer_extractor.extract = flaky
    result = consistency_extractor.measure_variance({"r1": "text"}, n_samples=3)
    assert result["nReports"] == 0 and len(result["reportsSkipped"]) == 1
    skipped = result["reportsSkipped"][0]
    assert skipped["reportId"] == "r1" and skipped["nSucceeded"] == 1 and len(skipped["failures"]) == 2
    assert result["behaviourAgreementRate"] is None


def test_consistency_returns_none_when_no_value_reaches_a_majority():
    calls = iter([
        {"behaviourId": "a", "requiredFields": [], "policyFields": [], "threshold": {"failureCount": 3}, "timeWindow": None, "provenance": {}},
        {"behaviourId": "b", "requiredFields": [], "policyFields": [], "threshold": {"failureCount": 4}, "timeWindow": None, "provenance": {}},
        {"behaviourId": "c", "requiredFields": [], "policyFields": [], "threshold": {"failureCount": 5}, "timeWindow": None, "provenance": {}},
    ])
    transformer_extractor.extract = lambda text, model=None: next(calls)
    result = consistency_extractor.extract("x", n_samples=3)
    assert result["threshold"] is None    # no value appears in a strict majority (2 of 3)


def _run_all():
    failures = 0
    names = sorted(n for n in globals() if n.startswith("test_") and callable(globals()[n]))
    for name in names:
        try:
            globals()[name]()
            print(f"OK   {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(names) - failures}/{len(names)} cases passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
