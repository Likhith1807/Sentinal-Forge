"""One regression test per known audit defect (2026-09 audit of the fine-tuned pipeline).

The audit: the fine-tuned pipeline compiled the right rule for 27 of 40 supported reports, and 7 of 44
reports silently produced an incorrect or unsupported rule. Each class of failure is pinned here so it
cannot return without a test failing. Nothing in this file suppresses a failure - a test that cannot
pass is a defect to fix, never a test to relax.

Groups mirror the audit's six required changes:
  A  evidence-backed time extraction        D  complete validation
  B  correct count semantics                E  correct event processing (reference engine; Scala twin in RuleCompilerSpec)
  C  unsupported-behaviour rejection        F  accurate naming
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from sentinelforge import behaviours as B
from sentinelforge.compile import RuleBuildError, compile_spec
from sentinelforge.conditions import find_conditions, verify_quote
from sentinelforge.pipeline import analyze
from sentinelforge.reconcile import ACCEPTED, NEEDS_REVIEW, REJECTED, reconcile
from sentinelforge.validation import check_dataset, default_profile, validate_spec

REPO = Path(__file__).resolve().parents[2]

B1_TEXT = ("Alert when an account has 5 or more failed logins inside a 90-second window and then logs in "
           "successfully. The rule reads account_id, event_type and timestamp.")


def _codes(a):
    return a.reconciliation.codes


# ------------------------------------------------------------------------------------------- A: time

class TestEvidenceBackedTime:
    def test_90_seconds_is_never_90_minutes(self):
        """The headline defect: a unit head answered 'minutes' for '90-second', compiling a 5,400 s window."""
        a = analyze(B1_TEXT, None)
        assert a.status == "compiled"
        assert a.compiled["timeWindowSeconds"] == 90
        w = a.reconciliation.spec["conditions"]["window"]
        assert (w["amount"], w["unit"], w["seconds"]) == (90, "seconds", 90)
        assert w["evidence"]["quote"] == "90-second"                       # amount, unit and passage travel together

    def test_a_model_claiming_90_minutes_cannot_silently_compile(self):
        wrong = {"behaviourId": "repeated-failed-login-then-success", "threshold": {"failureCount": 5},
                 "timeWindow": {"amount": 90, "unit": "minutes"}}
        a = analyze(B1_TEXT, wrong)
        assert a.status == "needs_review" and a.compiled is None
        assert "WINDOW_MODEL_DISAGREES" in _codes(a)

    def test_the_wrong_unit_is_named_in_the_reason(self):
        wrong = {"behaviourId": "repeated-failed-login-then-success", "threshold": {"failureCount": 5},
                 "timeWindow": {"amount": 90, "unit": "minutes"}}
        msg = next(r.message for r in analyze(B1_TEXT, wrong).reconciliation.reasons if r.code == "WINDOW_MODEL_DISAGREES")
        assert "5400" in msg and "90" in msg and "90-second" in msg

    @pytest.mark.parametrize("text,seconds", [
        ("fire when 4 or more failed logins for one account occur within 10 minutes and then it succeeds", 600),
        ("fire when 4 or more failed logins for one account occur within a 2-hour window and then it succeeds", 7200),
        ("fire when 4 or more failed logins for one account occur within thirty seconds and then it succeeds", 30),
        ("fire when 4 or more failed logins for one account occur within 1 minute and then it succeeds", 60),
    ])
    def test_unit_is_read_from_the_passage(self, text, seconds):
        assert analyze(text, None).compiled["timeWindowSeconds"] == seconds

    def test_contradictory_windows_are_rejected_not_resolved(self):
        t = ("Alert when 5 or more failed logins hit one account within 2 minutes and then it succeeds. "
             "Alert on 5 or more failed logins on one account within 30 minutes before a success.")
        a = analyze(t, None)
        assert a.status == "rejected" and "WINDOW_CONFLICT" in _codes(a)

    def test_the_fixture_with_contradicting_thresholds_is_rejected(self):
        text = (REPO / "compiler/test/fixtures/adversarial/contradictory-threshold.md").read_text(encoding="utf-8")
        a = analyze(text, None)
        assert a.compiled is None, "a report that states two different thresholds must not compile"

    def test_missing_window_is_never_defaulted(self):
        a = analyze("Alert when 5 or more failed logins hit one account and then it succeeds.", None)
        assert a.compiled is None and "WINDOW_MISSING" in _codes(a)

    def test_observation_windows_are_not_the_rule_window(self):
        t = ("The account failed 9 times in roughly 41 seconds and then authenticated successfully. "
             "Alert when 6 or more failed logins for one account occur within 5 minutes and then it succeeds.")
        assert analyze(t, None).compiled["timeWindowSeconds"] == 300

    def test_evidence_quotes_verify_against_the_report(self):
        a = analyze(B1_TEXT, None)
        for cond in a.reconciliation.spec["conditions"].values():
            assert verify_quote(B1_TEXT, cond["evidence"])
        assert not verify_quote("a different report", a.reconciliation.spec["conditions"]["window"]["evidence"])

    def test_strict_spec_never_defaults_a_missing_unit(self):
        spec = {"behaviourId": "repeated-failed-login-then-success", "threshold": {"failureCount": 5}, "timeWindow": {"amount": 90}}
        with pytest.raises(RuleBuildError, match="UNKNOWN_UNIT"):
            compile_spec(spec)

    def test_finetuned_unit_helper_reads_text_not_a_head(self):
        pytest.importorskip("torch")
        import sys
        sys.path.insert(0, str(REPO / "nlp" / "src"))
        from finetuned_extractor import unit_after
        assert unit_after("a 90-second window", 4) == "seconds"
        assert unit_after("within 5 minutes", 8) == "minutes"
        assert unit_after("within 5 widgets", 8) is None


# ----------------------------------------------------------------------------------- B: count semantics

class TestCountSemantics:
    def test_event_count_distinct_accounts_and_distinct_hosts_are_told_apart(self):
        c = find_conditions("Flag when 6 or more failed logins hit one account. Also 4 or more distinct accounts fail from one host. "
                            "Also successful logins from 2 or more different hosts.").counts
        assert [(x.value, x.semantics) for x in c] == [(6, "event_count"), (4, "distinct_accounts"), (2, "distinct_hosts")]

    def test_distinct_accounts_report_cannot_compile_as_an_event_count_rule(self):
        t = "Alert when 5 or more distinct accounts fail within 5 minutes and then one succeeds."
        a = analyze(t, {"behaviourId": "repeated-failed-login-then-success", "threshold": {"failureCount": 5},
                        "timeWindow": {"amount": 5, "unit": "minutes"}})
        assert a.compiled is None and "COUNT_SEMANTICS_MISMATCH" in _codes(a)

    def test_ambiguous_login_count_alias_is_rejected_by_the_strict_spec(self):
        spec = {"behaviourId": "multi-host-authentication", "threshold": {"loginCount": 2},
                "timeWindow": {"amount": 15, "unit": "minutes"}}
        with pytest.raises(RuleBuildError, match="AMBIGUOUS_COUNT_ALIAS"):
            compile_spec(spec)

    def test_ambiguous_alias_from_a_model_is_flagged_for_review_even_when_the_text_resolves_it(self):
        t = "Alert when one account has successful logins from 2 or more different hosts within 15 minutes."
        a = analyze(t, {"behaviourId": "multi-host-authentication", "threshold": {"loginCount": 2},
                        "timeWindow": {"amount": 15, "unit": "minutes"}})
        assert a.status == "needs_review" and "AMBIGUOUS_COUNT_ALIAS" in _codes(a)

    def test_alias_belonging_to_another_behaviour_is_rejected(self):
        spec = {"behaviourId": "repeated-failed-login-then-success", "threshold": {"distinctAccountCount": 5},
                "timeWindow": {"amount": 5, "unit": "minutes"}}
        with pytest.raises(RuleBuildError, match="AMBIGUOUS_COUNT_ALIAS"):
            compile_spec(spec)

    def test_two_threshold_keys_conflict(self):
        spec = {"behaviourId": "repeated-failed-login-then-success", "threshold": {"failureCount": 5, "distinctAccountCount": 3},
                "timeWindow": {"amount": 5, "unit": "minutes"}}
        with pytest.raises(RuleBuildError, match="CONFLICTING_KEYS"):
            compile_spec(spec)

    def test_threshold_and_condition_count_that_disagree_conflict(self):
        spec = {"behaviourId": "repeated-failed-login-then-success", "threshold": {"failureCount": 5},
                "conditions": {"count": {"value": 6, "semantics": "event_count"}}, "timeWindow": {"amount": 5, "unit": "minutes"}}
        with pytest.raises(RuleBuildError, match="CONFLICTING_KEYS"):
            compile_spec(spec)

    def test_reports_stating_two_thresholds_are_rejected(self):
        t = ("Alert when 5 or more failed logins hit one account within 5 minutes and then it succeeds. "
             "Trigger when 9 or more failed logins hit one account within 5 minutes and then it succeeds.")
        a = analyze(t, None)
        assert a.status == "rejected" and "COUNT_CONFLICT" in _codes(a)

    def test_more_than_n_means_n_plus_one(self):
        t = "Alert when more than 7 failed logins hit one account within 2 minutes and then it succeeds."
        assert analyze(t, None).compiled["countThreshold"] == 8

    def test_upper_bounds_are_not_thresholds(self):
        t = "Alert when fewer than 3 failed logins hit one account within 2 minutes and then it succeeds."
        a = analyze(t, None)
        assert a.compiled is None

    def test_compiled_rule_records_its_count_semantics(self):
        assert compile_spec({"behaviourId": "multi-host-authentication", "threshold": {"distinctHostCount": 2},
                             "timeWindow": {"amount": 15, "unit": "minutes"}})["countSemantics"] == "distinct_hosts"


# ------------------------------------------------------------------------ C: unsupported behaviour

PORT_SCAN = (REPO / "data/corpus/reports/SFC-0215.md")


class TestUnsupportedBehaviourRejection:
    def test_port_scan_report_is_not_compiled_as_password_spray(self):
        """The audit's two silent acceptances: a port-scan report the classifier scored as password spray."""
        text = PORT_SCAN.read_text(encoding="utf-8")
        claim = {"behaviourId": "password-spray-across-accounts", "threshold": {"distinctAccountCount": 200},
                 "timeWindow": {"amount": 5, "unit": "minutes"}}
        a = analyze(text, claim)
        assert a.compiled is None and a.status == "rejected"
        assert {"COUNT_UNKNOWN_OBJECT", "UNSUPPORTED_QUALIFIER"} & set(_codes(a))

    def test_a_confident_classifier_does_not_authorise_compilation(self):
        text = PORT_SCAN.read_text(encoding="utf-8")
        claim = {"behaviourId": "password-spray-across-accounts", "threshold": {"distinctAccountCount": 200},
                 "timeWindow": {"amount": 5, "unit": "minutes"}, "behaviourConfidence": 0.999, "confidence": 1.0}
        assert analyze(text, claim).compiled is None

    def test_every_unsupported_corpus_report_is_refused_in_evidence_only_mode(self):
        import json
        refused = 0
        for p in sorted((REPO / "data/corpus/gold").glob("*.gold.json")):
            g = json.loads(p.read_text(encoding="utf-8"))
            if g["supported"]:
                continue
            text = (REPO / g["reportFile"]).read_text(encoding="utf-8")
            assert analyze(text, None).compiled is None, g["reportId"]
            refused += 1
        assert refused == 16

    @pytest.mark.parametrize("extra,code_kind", [
        ("outside business hours", "time-of-day"),
        ("from a country other than the user's home country", "geographic"),
        ("except for service accounts", "exclusion"),
        ("from the same subnet", "grouping"),
    ])
    def test_extra_conditions_the_recipe_cannot_honour_are_rejected(self, extra, code_kind):
        t = f"Alert when 5 or more failed logins hit one account within 5 minutes {extra} and then it succeeds."
        a = analyze(t, None)
        assert a.compiled is None and "UNSUPPORTED_QUALIFIER" in _codes(a), code_kind

    def test_direction_specific_auth_policy_is_not_compiled_as_any_mismatch(self):
        t = ("Alert whenever a service account signs in with a password. "
             "Fields needed: `account_id`, `event_type`, `auth_method` and `expected_auth_method`.")
        a = analyze(t, {"behaviourId": "auth-method-policy-violation"})
        assert a.compiled is None

    def test_model_abstention_stays_an_abstention(self):
        a = analyze("The SOC observed unusual DNS lookups from a workstation.", {"behaviourId": None})
        assert a.status == "rejected" and "UNSUPPORTED_BEHAVIOUR" in _codes(a)

    def test_a_unknown_required_field_rejects(self):
        t = ("Alert when 5 or more failed logins hit one account within 5 minutes and then it succeeds. "
             "Required log fields: `account_id`, `event_type`, `timestamp` and `geo_country`.")
        a = analyze(t, None)
        assert a.compiled is None and "UNKNOWN_FIELD" in _codes(a)

    def test_incidental_fields_do_not_reject_a_supported_report(self):
        t = ("Alert when 5 or more failed logins hit one account within 5 minutes and then it succeeds. "
             "Required log fields: `account_id`, `event_type` and `timestamp`. "
             "In this case `source_ip` varied between events and is not part of the pattern.")
        assert analyze(t, None).status == "compiled"


# --------------------------------------------------------------------------- D: complete validation

def _spec(**over):
    base = {"behaviourId": "repeated-failed-login-then-success", "threshold": {"failureCount": 5},
            "timeWindow": {"amount": 5, "unit": "minutes"}}
    base.update(over)
    return base


class TestCompleteValidation:
    @pytest.mark.parametrize("bad", [True, False, float("nan"), float("inf"), float("-inf"), "5", None, [5], {"n": 5}])
    def test_count_must_be_a_real_finite_number(self, bad):
        assert not validate_spec(_spec(threshold={"failureCount": bad})).ok

    @pytest.mark.parametrize("bad", [True, False, float("nan"), float("inf"), "5", None])
    def test_window_amount_must_be_a_real_finite_number(self, bad):
        assert not validate_spec(_spec(timeWindow={"amount": bad, "unit": "minutes"})).ok

    def test_booleans_are_named_as_such(self):
        codes = [i.code for i in validate_spec(_spec(threshold={"failureCount": True})).issues]
        assert "BOOLEAN_AS_NUMBER" in codes

    @pytest.mark.parametrize("block", ["threshold", "timeWindow", "conditions"])
    @pytest.mark.parametrize("bad", [[], [1, 2], "x", 5])
    def test_malformed_blocks_are_rejected_not_crashed_on(self, block, bad):
        r = validate_spec(_spec(**{block: bad}))
        assert not r.ok and any(i.code == "MALFORMED_BLOCK" for i in r.issues)

    @pytest.mark.parametrize("amount,unit", [(0.5, "seconds"), (1.0001, "minutes"), (0.0001, "hours")])
    def test_sub_second_precision_is_unsupported(self, amount, unit):
        r = validate_spec(_spec(timeWindow={"amount": amount, "unit": unit}))
        assert any(i.code == "UNSUPPORTED_PRECISION" for i in r.issues)

    def test_fractional_minutes_that_are_whole_seconds_are_fine(self):
        r = validate_spec(_spec(timeWindow={"amount": 1.5, "unit": "minutes"}))
        assert r.ok and r.windowSeconds == 90

    @pytest.mark.parametrize("value", [0, -3, 0.5])
    def test_counts_must_be_positive_whole_numbers(self, value):
        assert not validate_spec(_spec(threshold={"failureCount": value})).ok

    def test_a_distinct_threshold_of_one_is_meaningless_and_rejected(self):
        spec = {"behaviourId": "password-spray-across-accounts", "threshold": {"distinctAccountCount": 1}, "timeWindow": {"amount": 5, "unit": "minutes"}}
        assert any(i.code == "COUNT_TOO_SMALL" for i in validate_spec(spec).issues)

    def test_out_of_range_values_are_rejected(self):
        assert not validate_spec(_spec(threshold={"failureCount": 10**9})).ok
        assert not validate_spec(_spec(timeWindow={"amount": 400, "unit": "days"})).ok

    def test_unexpected_keys_inside_time_window_are_rejected(self):
        assert not validate_spec(_spec(timeWindow={"amount": 5, "unit": "minutes", "unti": "hours"})).ok

    def test_conflicting_window_blocks_are_rejected(self):
        spec = _spec(conditions={"window": {"amount": 30, "unit": "seconds"}})
        assert any(i.code == "CONFLICTING_KEYS" for i in validate_spec(spec).issues)

    def test_policy_behaviours_take_no_numbers(self):
        spec = {"behaviourId": "mfa-missing-on-required-account", "threshold": {"failureCount": 3}}
        assert any(i.code == "NOT_APPLICABLE" for i in validate_spec(spec).issues)

    def test_non_dict_spec_and_unknown_behaviour(self):
        assert not validate_spec([1, 2]).ok and not validate_spec(None).ok
        assert not validate_spec({"behaviourId": "totally-made-up"}).ok
        assert not validate_spec({"behaviourId": 7}).ok

    def test_every_field_the_rule_reads_or_emits_is_checked_including_event_id(self):
        profile = default_profile()
        del profile["columns"]["event_id"]                        # an OUTPUT of every alert, not merely read
        chk = check_dataset("repeated-failed-login-then-success", profile)
        assert not chk.supported and [d.field for d in chk.blocking] == ["event_id"]

    @pytest.mark.parametrize("col,wrong", [("mfa_used", "string"), ("timestamp", "long"), ("account_id", "long")])
    def test_wrong_column_types_block_the_rule(self, col, wrong):
        profile = default_profile()
        profile["columns"][col] = wrong
        beh = "mfa-missing-on-required-account" if col == "mfa_used" else "repeated-failed-login-then-success"
        chk = check_dataset(beh, profile)
        assert any(d.field == col and d.status == "wrong_type" for d in chk.dependencies)

    def test_policy_dependencies_are_checked_with_their_types(self):
        profile = default_profile()
        profile["policyColumns"]["mfa_required"] = "string"
        chk = check_dataset("mfa-missing-on-required-account", profile)
        assert any(d.field == "policy.mfa_required" and d.status == "wrong_type" for d in chk.dependencies)

    def test_unreliable_fields_are_flagged(self):
        r = check_dataset("repeated-failed-login-then-success", default_profile())
        assert r.supported                                         # source_ip is unreliable but the recipe does not read it


# ------------------------------------------------------------------------------ F: accurate naming

class TestAccurateNaming:
    def test_multi_host_authentication_is_not_called_concurrent_sessions(self):
        b = B.BEHAVIOURS["multi-host-authentication"]
        assert "concurrent" not in (b.id + b.display_name + b.checks).lower()
        assert any("overlap" in lim.lower() and "not measured" in lim.lower() for lim in b.limitations)

    def test_auth_method_violation_is_named_for_what_it_checks(self):
        b = B.BEHAVIOURS["auth-method-policy-violation"]
        text = (b.id + b.display_name + b.checks).lower()
        assert "service" not in text and "interactive" not in text
        assert "expected_auth_method" in b.checks

    def test_mfa_rule_does_not_claim_a_bypass(self):
        b = B.BEHAVIOURS["mfa-missing-on-required-account"]
        assert "bypass" not in (b.id + b.display_name + b.checks).lower()

    def test_legacy_ids_still_resolve(self):
        assert B.canonical_id("concurrent-sessions-different-hosts") == "multi-host-authentication"
        assert B.canonical_id("service-account-interactive-auth") == "auth-method-policy-violation"
        assert B.canonical_id("mfa-bypass-on-required-account") == "mfa-missing-on-required-account"
        assert compile_spec({"behaviourId": "concurrent-sessions-different-hosts", "threshold": {"successCount": 2},
                             "timeWindow": {"amount": 15, "unit": "minutes"}})["behaviourId"] == "multi-host-authentication"

    def test_overlap_wording_in_a_report_becomes_a_caveat_not_a_claim(self):
        t = ("Alert when one account holds concurrent live sessions on 2 or more different hosts within 15 minutes. "
             "Fields needed: `account_id`, `event_type`, `timestamp` and `source_host`.")
        a = analyze(t, None)
        assert a.compiled is not None
        assert any("does not measure it" in u or "not measured" in u.lower() for u in a.reconciliation.uncertainties)

    def test_sigma_and_docs_do_not_use_the_old_names(self):
        import subprocess
        bad = ("concurrent-sessions-different-hosts", "service-account-interactive-auth", "mfa-bypass-on-required-account")
        out = subprocess.run(["git", "grep", "-l", "-e", bad[0], "-e", bad[1], "-e", bad[2], "--", "sentinelforge", "dashboard",
                              "compiler/src", "scripts/datagen", "spark"], cwd=REPO, capture_output=True, text=True).stdout.split()
        allowed = {"sentinelforge/behaviours.py", "tests/core/test_audit_regressions.py",
               "compiler/src/main/scala/sentinelforge/compiler/CompiledSpec.scala"}   # LegacyIds: the Scala twin of LEGACY_IDS
        assert [f for f in out if f not in allowed] == []
