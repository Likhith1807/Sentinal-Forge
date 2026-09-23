"""HTTP surface: the workflow over the wire, plus access control, input limits and error hygiene."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from dashboard.backend.main import create_app
from sentinelforge.service.settings import Settings

REPO = Path(__file__).resolve().parents[2]
BRUTE = (REPO / "data/demo/reports/01-brute-force-vpn.md").read_text(encoding="utf-8")


def make_client(tmp_path, client=("127.0.0.1", 50000), **settings):
    app = create_app(Settings(state_dir=tmp_path / "state", engine="reference", **settings))
    return TestClient(app, client=client)


def wait_job(c, job_id, headers=None, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job_id}", headers=headers).json()
        if j["state"] in ("ready", "needs_review", "rejected", "completed", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def full_flow(c, headers=None, text=BRUTE):
    r = c.post("/api/reports", json={"text": text}, headers=headers)
    assert r.status_code == 201, r.text
    a = c.post(f"/api/reports/{r.json()['id']}/analyses", json={"extractor": "evidence"}, headers=headers).json()
    assert wait_job(c, a["jobId"], headers)["state"] == "ready"
    rv = c.post(f"/api/analyses/{a['analysisId']}/rule", headers=headers)
    assert rv.status_code == 201, rv.text
    run = c.post(f"/api/rule-versions/{rv.json()['id']}/runs", json={}, headers=headers).json()
    assert wait_job(c, run["jobId"], headers)["state"] == "completed"
    return rv.json(), run


def test_the_workflow_over_http(tmp_path):
    with make_client(tmp_path) as c:
        rv, run = full_flow(c)
        alerts = c.get(f"/api/runs/{run['runId']}/alerts").json()
        assert alerts["total"] >= 1
        ev = c.get(f"/api/runs/{run['runId']}/alerts/{alerts['alerts'][0]['triggeringEventId']}/evidence").json()
        assert ev["executed"]["ruleVersionId"] == rv["id"] and ev["why"]["passages"]
        d = c.post(f"/api/rule-versions/{rv['id']}/decision", json={"decision": "approve", "runId": run["runId"], "note": "ok"})
        assert d.status_code == 200 and d.json()["state"] == "approved"
        assert "run_dir" not in c.get(f"/api/runs/{run['runId']}").json(), "server paths must not reach clients"


def test_the_client_cannot_supply_a_specification_or_an_analyst_name(tmp_path):
    with make_client(tmp_path) as c:
        rv, run = full_flow(c)
        r = c.post(f"/api/rule-versions/{rv['id']}/decision",
                   json={"decision": "approve", "runId": run["runId"], "analyst": "impersonated", "spec": {"behaviourId": "x"}})
        assert r.status_code == 200
        assert r.json()["decisions"][-1]["analyst"] == "local-analyst", "the analyst identity comes from authentication"


def test_unknown_and_malformed_ids_are_404_or_422_not_500(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get("/api/runs/does-not-exist").status_code == 404
        assert c.get("/api/reports/..%2F..%2Fetc%2Fpasswd").status_code in (404, 422)
        assert c.get("/api/jobs/nope").status_code == 404
        assert c.post("/api/analyses/nope/rule").status_code == 404
        assert c.post("/api/reports/does-not-exist/analyses", json={}).status_code == 404


def test_validation_errors_are_422_with_a_reason(tmp_path):
    with make_client(tmp_path) as c:
        assert c.post("/api/reports", json={"text": "tiny"}).status_code == 422
        assert c.post("/api/reports", json={}).status_code == 422
        rv, _ = full_flow(c)
        r = c.post(f"/api/rule-versions/{rv['id']}/refine", json={"overrides": {"count": True}, "note": "because"})
        assert r.status_code == 422 and "BOOLEAN_AS_NUMBER" in str(r.json()["issues"])


def test_blocked_runs_return_409_with_the_exact_dependencies(tmp_path):
    with make_client(tmp_path) as c:
        r = c.post("/api/reports", json={"text": (REPO / "data/demo/reports/02-password-spray.md").read_text(encoding="utf-8")}).json()
        a = c.post(f"/api/reports/{r['id']}/analyses", json={"extractor": "evidence"}).json()
        wait_job(c, a["jobId"])
        rv = c.post(f"/api/analyses/{a['analysisId']}/rule").json()
        impact = c.post("/api/datasets/demo-auth/schema-change", json={"drop": ["source_host"]}).json()
        assert impact["summary"]["affected"] == 1
        blocked = c.post(f"/api/rule-versions/{rv['id']}/runs", json={})
        assert blocked.status_code == 409 and "paused" in blocked.json()["detail"]
        assert c.post(f"/api/rule-versions/{rv['id']}/resume").status_code == 409
        assert c.post("/api/datasets/demo-auth/restore", json={"version": 1}).status_code == 200
        res = c.post(f"/api/rule-versions/{rv['id']}/resume")
        assert res.status_code == 202 and wait_job(c, res.json()["jobId"])["state"] == "completed"
        assert c.get(f"/api/rule-versions/{rv['id']}").json()["state"] == "draft"


def test_security_headers_and_no_store(tmp_path):
    with make_client(tmp_path) as c:
        r = c.get("/api/config")
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
        assert "default-src 'self'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"


def test_oversized_bodies_are_refused(tmp_path):
    with make_client(tmp_path, max_report_bytes=1000) as c:
        assert c.post("/api/reports", content=b"{" + b"x" * 20000, headers={"content-type": "application/json"}).status_code == 413
        assert c.post("/api/reports", json={"text": "y" * 2000}).status_code == 422


def test_unhandled_errors_do_not_leak_tracebacks(tmp_path):
    app = create_app(Settings(state_dir=tmp_path / "state", engine="reference"))

    def boom():
        raise RuntimeError("secret internal detail /home/x/.env")
    app.router.routes.insert(0, APIRoute("/api/boom", boom, methods=["GET"]))     # ahead of the static mount
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/boom")
        assert r.status_code == 500 and "secret" not in r.text and "Traceback" not in r.text and "requestId" in r.json()


# ---------------------------------------------------------------------------------- access control

def test_local_demo_mode_refuses_non_loopback_clients(tmp_path):
    with make_client(tmp_path, client=("203.0.113.9", 4000)) as c:
        assert c.get("/api/config").status_code == 403
        assert c.post("/api/reports", json={"text": BRUTE}).status_code == 403
        assert c.get("/api/health").status_code == 200            # liveness only
    with make_client(tmp_path / "b") as c:
        assert c.get("/api/config", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 403


def test_token_mode_requires_a_valid_bearer_token(tmp_path):
    with make_client(tmp_path, client=("203.0.113.9", 4000), api_tokens="ana:tok-ana:analyst,vic:tok-vic:viewer") as c:
        assert c.get("/api/config").status_code == 401
        assert c.get("/api/config", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/config", headers={"Authorization": "Basic abc"}).status_code == 401
        ok = c.get("/api/config", headers={"Authorization": "Bearer tok-ana"})
        assert ok.status_code == 200 and ok.json()["user"] == {"name": "ana", "role": "analyst"} and ok.json()["auth"] == "token"


def test_viewers_can_read_but_never_write(tmp_path):
    with make_client(tmp_path, api_tokens="ana:tok-ana:analyst,vic:tok-vic:viewer") as c:
        ana, vic = {"Authorization": "Bearer tok-ana"}, {"Authorization": "Bearer tok-vic"}
        rv, run = full_flow(c, ana)
        assert c.get("/api/overview", headers=vic).status_code == 200
        assert c.get(f"/api/runs/{run['runId']}/alerts", headers=vic).status_code == 200
        for method, url, body in [("post", "/api/reports", {"text": BRUTE}), ("post", f"/api/rule-versions/{rv['id']}/runs", {}),
                                  ("post", f"/api/rule-versions/{rv['id']}/decision", {"decision": "approve", "runId": run["runId"]}),
                                  ("post", "/api/datasets/demo-auth/schema-change", {"drop": ["source_ip"]})]:
            assert getattr(c, method)(url, json=body, headers=vic).status_code == 403, url
        d = c.post(f"/api/rule-versions/{rv['id']}/decision", json={"decision": "approve", "runId": run["runId"]}, headers=ana)
        assert d.json()["decisions"][-1]["analyst"] == "ana"


def test_state_changing_calls_are_rate_limited(tmp_path):
    with make_client(tmp_path, rate_limit_per_minute=5) as c:
        codes = [c.post("/api/reports", json={"text": BRUTE}).status_code for _ in range(8)]
        assert codes[:5] == [201] * 5 and 429 in codes[5:]
        assert c.get("/api/overview").status_code == 200           # reads are not throttled by the write bucket


def test_dataset_upload_is_validated_and_bounded(tmp_path):
    with make_client(tmp_path, max_upload_bytes=5000) as c:
        assert c.post("/api/datasets", json={"name": "x", "eventsJsonl": "not json\n"}).status_code == 422
        assert c.post("/api/datasets", json={"name": "x", "eventsJsonl": '["a list, not an object"]\n'}).status_code == 422
        assert c.post("/api/datasets", json={"name": "x", "eventsJsonl": "y" * 12000}).status_code in (413, 422)
        ok = c.post("/api/datasets", json={"name": "tiny", "eventsJsonl": '{"event_id":"e1","timestamp":"2026-03-01T00:00:00Z","account_id":"a","event_type":"login_failure"}\n'})
        assert ok.status_code == 201 and ok.json()["profile"]["columns"]["event_id"] == "string"
        assert "eventsPath" not in ok.json()


def test_static_ui_is_served_with_the_security_headers(tmp_path):
    with make_client(tmp_path) as c:
        r = c.get("/")
        assert r.status_code == 200 and "content-security-policy" in r.headers


def test_sigma_export_refuses_what_it_cannot_preserve(tmp_path):
    with make_client(tmp_path) as c:
        rv, _ = full_flow(c)                                        # repeated failed logins -> refused
        s = c.get(f"/api/rule-versions/{rv['id']}/sigma").json()
        assert s["status"] == "rejected" and "Sigma" in s["reason"] and s["sigma"] is None
        r = c.post("/api/reports", json={"text": (REPO / "data/demo/reports/02-password-spray.md").read_text(encoding="utf-8")}).json()
        a = c.post(f"/api/reports/{r['id']}/analyses", json={"extractor": "evidence"}).json()
        wait_job(c, a["jobId"])
        spray = c.post(f"/api/analyses/{a['analysisId']}/rule").json()
        s = c.get(f"/api/rule-versions/{spray['id']}/sigma").json()
        assert s["status"] == "exported" and "value_count" in s["sigma"] and "gte: 4" in s["sigma"] and "600s" in s["sigma"]
        assert s["notPreserved"], "declared differences must accompany every export"
