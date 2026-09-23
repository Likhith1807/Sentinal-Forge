"""The workflow the product exists for, exercised end to end against the demonstration dataset.

Uses the reference engine (SF_ENGINE=reference) so it runs anywhere; the Spark engine is covered by
tests/integration and by the differential test, which pin the two to the same behaviour.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from sentinelforge.service.settings import Settings
from sentinelforge.service.workflow import (Blocked, InvalidRequest, NotFound, StateConflict, Workspace)

REPO = Path(__file__).resolve().parents[2]

SPRAY = ("Password spraying from one host. Alert when {n} or more distinct accounts fail from the same source host within "
         "{w}. Required log fields: `account_id`, `event_type`, `timestamp` and `source_host`.")
BRUTE = ("Alert when {n} or more failed logins for one account occur within {w} and then it logs in successfully. "
         "Required log fields: `account_id`, `event_type` and `timestamp`.")
MULTIHOST = ("Flag one account that authenticates successfully from {n} or more different hosts within {w}. "
             "Fields needed: `account_id`, `event_type`, `timestamp` and `source_host`.")
MFA = ("Alert when a successful login is recorded without MFA for an account whose policy requires MFA. "
       "The rule reads `account_id`, `event_type`, `mfa_used` and `mfa_required`.")
AUTHM = ("Flag successful logins that do not use the account's expected authentication method. "
         "Fields needed: `account_id`, `event_type`, `auth_method` and `expected_auth_method`.")
PORTSCAN = (REPO / "data/corpus/reports/SFC-0215.md").read_text(encoding="utf-8")


@pytest.fixture()
def ws(tmp_path):
    w = Workspace(Settings(state_dir=tmp_path / "state", engine="reference", job_workers=2))
    yield w
    w.shutdown()


def analyse(ws, text, extractor="evidence"):
    r = ws.submit_analysis(ws.create_report(text)["id"], extractor)
    ws.jobs.wait(r["jobId"])
    return ws.get_analysis(r["analysisId"])


def make_rule(ws, text):
    a = analyse(ws, text)
    assert a["status"] == "ready", a["result"]["reconciliation"]["reasons"]
    return ws.create_rule_version(a["id"])


def run(ws, rv, dataset=None):
    r = ws.submit_run(rv["id"], dataset)
    job = ws.jobs.wait(r["jobId"])
    assert job["state"] == "completed", job
    return ws.get_run(r["runId"])


# ----------------------------------------------------------------------------- the whole loop

def test_paste_analyse_compile_run_inspect_approve(ws):
    a = analyse(ws, SPRAY.format(n=4, w="10 minutes"))
    assert a["status"] == "ready"
    assert [h["state"] for h in ws.jobs.get(a["jobId"])["history"]] == ["queued", "extracting", "validating", "ready"]
    rv = ws.create_rule_version(a["id"], user="ana")
    assert rv["state"] == "draft" and rv["compiled"]["distinctThreshold"] == 4 and rv["compiled"]["timeWindowSeconds"] == 600
    r = run(ws, rv)
    assert r["state"] == "completed" and r["engine"] == "python-reference" or r["engine"] == "reference"
    assert r["freshness"]["origin"] == "fresh" and r["freshness"]["dataKind"] == "demonstration"
    alerts = ws.alerts(r["id"])
    assert alerts["total"] >= 1 and all(a["status"] == "alert" for a in alerts["alerts"])
    rec = ws.evidence_record(r["id"], alerts["alerts"][0]["triggeringEventId"])
    assert set(rec) >= {"why", "means", "supported", "fired", "executed", "uncertain"}
    assert rec["why"]["passages"] and all(p["quoteVerified"] for p in rec["why"]["passages"])
    assert rec["executed"]["ruleHash"] == rv["rule_hash"] and rec["executed"]["runId"] == r["id"]
    assert rec["fired"]["supportingEvents"], "the alert must list its supporting events"
    approved = ws.decide(rv["id"], "approve", "ana", "looks right", run_id=r["id"])
    assert approved["state"] == "approved" and approved["decisions"][-1]["run_id"] == r["id"]


def test_changing_a_report_condition_changes_the_rule_and_its_execution(ws):
    counts = {}
    for n, w in ((3, "2 minutes"), (5, "2 minutes"), (8, "2 minutes"), (5, "10 minutes")):
        rv = make_rule(ws, BRUTE.format(n=n, w=w))
        r = run(ws, rv)
        counts[(n, w)] = (rv["compiled"]["countThreshold"], rv["compiled"]["timeWindowSeconds"], ws.alerts(r["id"])["total"], rv["rule_hash"])
    assert counts[(3, "2 minutes")][:2] == (3, 120) and counts[(5, "10 minutes")][:2] == (5, 600)
    a3, a5, a8 = (counts[(n, "2 minutes")][2] for n in (3, 5, 8))
    assert a3 > a5 > a8 >= 1, counts                                  # a stricter threshold catches fewer planted incidents
    assert counts[(5, "10 minutes")][2] >= a5                          # a wider window catches at least as many
    assert len({v[3] for v in counts.values()}) == 4                   # four different rules, four different hashes


def test_all_five_behaviours_run_through_the_same_workflow(ws):
    for text in (BRUTE.format(n=5, w="2 minutes"), SPRAY.format(n=4, w="10 minutes"), MULTIHOST.format(n=2, w="15 minutes"), AUTHM, MFA):
        rv = make_rule(ws, text)
        r = run(ws, rv)
        assert ws.alerts(r["id"])["total"] >= 1, rv["behaviour_id"]


def test_unsupported_reports_are_rejected_with_reasons_and_cannot_become_rules(ws):
    a = analyse(ws, PORTSCAN)
    assert a["status"] == "rejected"
    codes = [r["code"] for r in a["result"]["reconciliation"]["reasons"]]
    assert "COUNT_UNKNOWN_OBJECT" in codes or "UNSUPPORTED_QUALIFIER" in codes
    with pytest.raises(StateConflict):
        ws.create_rule_version(a["id"])


def test_missing_evidence_needs_review_rather_than_a_guess(ws):
    a = analyse(ws, "Alert when 5 or more failed logins for one account occur and then it logs in successfully. Fields: `account_id`, `event_type`.")
    assert a["status"] == "needs_review"
    assert any(r["code"] == "WINDOW_MISSING" for r in a["result"]["reconciliation"]["reasons"])


def test_a_report_is_bounded_and_sanitised(ws):
    with pytest.raises(InvalidRequest):
        ws.create_report("x" * (ws.settings.max_report_bytes + 1))
    with pytest.raises(InvalidRequest):
        ws.create_report("short")
    with pytest.raises(InvalidRequest):
        ws.create_report("a valid looking report text\x00 with a NUL byte inside it")
    with pytest.raises(InvalidRequest):
        ws.submit_analysis(ws.create_report(BRUTE.format(n=5, w="2 minutes"))["id"], "not-an-extractor")
    with pytest.raises(NotFound):
        ws.get_report("does-not-exist")
    with pytest.raises(InvalidRequest):
        ws.get_report("../../etc/passwd")


# --------------------------------------------------------------------- approval and refinement

def test_approval_requires_a_completed_run_of_exactly_that_version(ws):
    rv1 = make_rule(ws, BRUTE.format(n=5, w="2 minutes"))
    rv2 = make_rule(ws, BRUTE.format(n=6, w="2 minutes"))
    r1 = run(ws, rv1)
    with pytest.raises(InvalidRequest):
        ws.decide(rv2["id"], "approve", "ana", run_id=r1["id"])         # another version's run
    with pytest.raises(InvalidRequest):
        ws.decide(rv1["id"], "approve", "ana")                           # no run at all
    assert ws.decide(rv1["id"], "approve", "ana", run_id=r1["id"])["state"] == "approved"
    with pytest.raises(StateConflict):
        ws.decide(rv1["id"], "approve", "bob", run_id=r1["id"])         # already approved


def test_concurrent_approvals_have_exactly_one_winner(ws):
    rv = make_rule(ws, BRUTE.format(n=5, w="2 minutes"))
    r = run(ws, rv)
    results = []

    def approve(name):
        try:
            ws.decide(rv["id"], "approve", name, run_id=r["id"])
            results.append("ok")
        except StateConflict:
            results.append("conflict")
    threads = [threading.Thread(target=approve, args=(f"a{i}",)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["conflict"] * 7 + ["ok"]
    assert len([d for d in ws.get_rule_version(rv["id"])["decisions"] if d["decision"] == "approve"]) == 1


def test_refine_builds_a_new_server_side_version_and_records_the_departure_from_the_report(ws):
    rv = make_rule(ws, BRUTE.format(n=5, w="2 minutes"))
    new = ws.refine_rule_version(rv["id"], {"count": 8}, "the SOC wants a higher bar", user="ana")
    assert new["version"] == 2 and new["origin"] == "analyst-refined" and new["compiled"]["countThreshold"] == 8
    assert new["compiled"]["timeWindowSeconds"] == 120 and new["rule_hash"] != rv["rule_hash"]
    assert new["overrides"]["from"] == {"count": 5} and new["overrides"]["to"] == {"count": 8}
    assert ws.get_rule_version(rv["id"])["state"] == "superseded"
    r = run(ws, new)
    rec = ws.evidence_record(r["id"], ws.alerts(r["id"])["alerts"][0]["triggeringEventId"])
    assert any("NOT backed by the report" in u for u in rec["uncertain"]["items"])
    assert rec["executed"]["origin"] == "analyst-refined"


@pytest.mark.parametrize("bad", [{"count": True}, {"count": 0}, {"count": -3}, {"count": float("nan")}, {"count": "5"},
                                 {"windowSeconds": 0.5}, {"windowSeconds": float("inf")}, {"unknown": 1}, {}])
def test_refine_is_strictly_validated(ws, bad):
    rv = make_rule(ws, BRUTE.format(n=5, w="2 minutes"))
    with pytest.raises(InvalidRequest):
        ws.refine_rule_version(rv["id"], bad, "because reasons")
    assert ws.get_rule_version(rv["id"])["state"] == "draft"             # a refused refinement changes nothing


def test_refine_needs_a_reason_and_cannot_touch_policy_rules(ws):
    rv = make_rule(ws, BRUTE.format(n=5, w="2 minutes"))
    with pytest.raises(InvalidRequest):
        ws.refine_rule_version(rv["id"], {"count": 6}, "")
    with pytest.raises(InvalidRequest):
        ws.refine_rule_version(make_rule(ws, MFA)["id"], {"count": 3}, "no numbers here")


# ------------------------------------------------------------------------- schema-change impact

def test_removing_source_host_pauses_exactly_the_rules_that_need_it_and_explains_why(ws):
    rules = {name: make_rule(ws, text) for name, text in {
        "brute": BRUTE.format(n=5, w="2 minutes"), "spray": SPRAY.format(n=4, w="10 minutes"),
        "multi": MULTIHOST.format(n=2, w="15 minutes"), "mfa": MFA, "authm": AUTHM}.items()}
    impact = ws.apply_schema_change("demo-auth", ["source_host"], None, user="ana")
    affected = {a["behaviourId"] for a in impact["affected"]}
    assert affected == {"password-spray-across-accounts", "multi-host-authentication"}
    assert {u["behaviourId"] for u in impact["unaffected"]} == {"repeated-failed-login-then-success", "mfa-missing-on-required-account",
                                                                "auth-method-policy-violation"}
    spray = next(a for a in impact["affected"] if a["behaviourId"] == "password-spray-across-accounts")
    blocked = {b["condition"]: b["because"] for b in spray["blockedConditions"]}
    assert any("Group events per source_host" in c for c in blocked)
    assert all(w["field"] == "source_host" and w["status"] == "missing" for ws_ in blocked.values() for w in ws_)
    # states really changed, and only for the affected rules
    assert ws.get_rule_version(rules["spray"]["id"])["state"] == "paused" and ws.get_rule_version(rules["multi"]["id"])["state"] == "paused"
    for k in ("brute", "mfa", "authm"):
        assert ws.get_rule_version(rules[k]["id"])["state"] == "draft"
    pr = ws.get_rule_version(rules["spray"]["id"])["pause_reason"]
    assert pr["fromVersion"] == 1 and pr["toVersion"] == 2 and pr["blockedConditions"]


def test_a_paused_rule_cannot_run_and_unaffected_rules_still_do(ws):
    spray, brute = make_rule(ws, SPRAY.format(n=4, w="10 minutes")), make_rule(ws, BRUTE.format(n=5, w="2 minutes"))
    ws.apply_schema_change("demo-auth", ["source_host"], None)
    with pytest.raises(StateConflict, match="paused"):
        ws.submit_run(spray["id"], "demo-auth")
    r = run(ws, brute, "demo-auth")                                      # unaffected: runs on the changed data
    assert r["state"] == "completed" and r["dataset"]["version"] == 2 and ws.alerts(r["id"])["total"] >= 1


def test_a_new_rule_for_a_broken_dependency_is_blocked_not_weakened(ws):
    ws.apply_schema_change("demo-auth", ["source_host"], None)
    a = analyse(ws, SPRAY.format(n=4, w="10 minutes"))
    assert a["status"] == "rejected" and a["result"]["rejectionKind"] == "data"
    with pytest.raises(StateConflict):
        ws.create_rule_version(a["id"])


def test_a_type_change_is_treated_like_a_missing_field(ws):
    mfa = make_rule(ws, MFA)
    impact = ws.apply_schema_change("demo-auth", None, {"mfa_used": "string"})
    aff = impact["affected"]
    assert [a["behaviourId"] for a in aff] == ["mfa-missing-on-required-account"]
    why = aff[0]["blockedConditions"][0]["because"][0]
    assert why["status"] == "wrong_type" and why["expectedType"] == "boolean" and why["actualType"] == "string"
    assert ws.get_rule_version(mfa["id"])["state"] == "paused"


def test_resume_needs_the_data_fixed_and_a_successful_revalidation_run(ws):
    spray = make_rule(ws, SPRAY.format(n=4, w="10 minutes"))
    assert ws.decide(spray["id"], "approve", "ana", run_id=run(ws, spray)["id"])["state"] == "approved"
    ws.apply_schema_change("demo-auth", ["source_host"], None)
    with pytest.raises(Blocked) as blocked:                              # data still broken -> cannot resume
        ws.resume_rule_version(spray["id"])
    assert [d["field"] for d in blocked.value.dependencies] == ["source_host"]
    assert ws.get_rule_version(spray["id"])["state"] == "paused"
    impact = ws.restore_dataset("demo-auth", 1)                          # the column is back (new version 3)
    assert [r["ruleVersionId"] for r in impact["resumable"]] == [spray["id"]]
    assert ws.get_rule_version(spray["id"])["state"] == "paused", "restoring data must not silently resume a rule"
    r = ws.resume_rule_version(spray["id"])
    assert ws.jobs.wait(r["jobId"])["state"] == "completed"
    back = ws.get_rule_version(spray["id"])
    assert back["state"] == "approved" and back["pause_reason"] is None    # returns to the state it had before the pause
    assert [d["decision"] for d in back["decisions"]][-1] == "resume"
    assert ws.get_run(r["runId"])["purpose"] == "revalidate"


def test_stale_results_are_labelled(ws):
    rv = make_rule(ws, BRUTE.format(n=5, w="2 minutes"))
    r = run(ws, rv)
    assert not ws.get_run(r["id"])["freshness"]["stale"]
    ws.apply_schema_change("demo-auth", ["source_ip"], None)
    fr = ws.get_run(r["id"])["freshness"]
    assert fr["stale"] and "changed since" in fr["label"]


def test_schema_changes_are_validated(ws):
    with pytest.raises(InvalidRequest):
        ws.apply_schema_change("demo-auth", ["no_such_column"], None)
    with pytest.raises(NotFound):
        ws.apply_schema_change("missing-dataset", ["source_host"], None)


# ------------------------------------------------------------------------------ jobs / overview

def test_interrupted_jobs_are_marked_failed_on_restart(tmp_path):
    settings = Settings(state_dir=tmp_path / "s", engine="reference")
    w = Workspace(settings)
    with w.store.tx() as c:
        c.execute("INSERT INTO jobs(id, kind, state, ref, detail, history, created_at, updated_at) VALUES ('j1','run','running','r1','{}','[]','t','t')")
    w.shutdown()
    w2 = Workspace(settings)
    job = w2.jobs.get("j1")
    assert job["state"] == "failed" and job["error"]["kind"] == "interrupted"
    w2.shutdown()


def test_overview_reports_paused_rules_and_data_health(ws):
    rv = make_rule(ws, SPRAY.format(n=4, w="10 minutes"))
    run(ws, rv)
    ws.apply_schema_change("demo-auth", ["source_host"], None)
    o = ws.overview()
    assert o["ruleCounts"]["paused"] == 1 and o["pausedRules"]
    kinds = {h["kind"] for h in o["dataHealth"]}
    assert "rule-paused" in kinds and "quarantined-rows" in kinds and "unreliable-field" in kinds
    assert o["datasets"][0]["version"] == 2 and o["recentRuns"]
