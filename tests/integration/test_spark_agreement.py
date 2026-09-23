"""Spark executor vs. reference engine on the golden scenarios and a small adversarial batch.

Needs a JVM and sbt, so it is opt-in:   pytest -m integration
CI runs it in the `integration` job (see .github/workflows/ci.yml).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "verify"))

from differential import run_behaviour  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402
from sentinelforge import spark_engine  # noqa: E402
from sentinelforge.compile import compile_spec  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine():
    try:
        spark_engine.find_java()
        spark_engine.ensure_built()
    except spark_engine.EngineUnavailable as exc:      # pragma: no cover - environment dependent
        pytest.skip(str(exc))


@pytest.mark.parametrize("bid", B.BEHAVIOUR_IDS)
def test_engines_agree_on_adversarial_scenarios(engine, bid, tmp_path):
    result = run_behaviour(bid, cases=24, seed=101, workdir=tmp_path)
    assert result["problems"] == [], json.dumps(result["problems"][:3], indent=1)
    assert result["scenarios"] == 24


def test_run_directory_is_self_describing_and_atomic(engine, tmp_path):
    spec = compile_spec({"behaviourId": "repeated-failed-login-then-success", "threshold": {"failureCount": 2},
                         "timeWindow": {"amount": 10, "unit": "seconds"}})
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    rows = [{"event_id": f"e{i}", "timestamp": f"2026-03-01T00:00:0{i}Z", "account_id": "a",
             "event_type": "login_failure" if i < 4 else "login_success", "source_host": "h", "source_ip": None,
             "auth_method": "password", "mfa_used": False, "session_id": None} for i in range(1, 5)]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    run = spark_engine.run_rule(tmp_path / "spec.json", tmp_path / "events.jsonl", tmp_path / "out")
    assert run["status"] == "completed" and run["ruleHash"] == spec["ruleHash"]
    assert run["compilerVersion"] == spec["compilerVersion"] and run["counts"]["alertsWritten"] == 1
    assert not list((tmp_path / "out").glob("*.tmp")), "no half-written temp files may remain"
    alert = spark_engine.read_alerts(tmp_path / "out")[0]
    assert alert["triggeringEventId"] == "e4" and [e["eventId"] for e in alert["evidence"]] == ["e1", "e2", "e3"]


def test_a_dataset_missing_a_required_column_fails_with_a_structured_error_and_is_preserved(engine, tmp_path):
    spec = compile_spec({"behaviourId": "password-spray-across-accounts", "threshold": {"distinctAccountCount": 3},
                         "timeWindow": {"amount": 60, "unit": "seconds"}})
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (tmp_path / "events.jsonl").write_text(json.dumps({"event_id": "e1", "timestamp": "2026-03-01T00:00:00Z", "account_id": "a",
                                                        "event_type": "login_failure"}) + "\n", encoding="utf-8")
    run = spark_engine.run_rule(tmp_path / "spec.json", tmp_path / "events.jsonl", tmp_path / "out")
    assert run["status"] == "failed" and run["error"]["kind"] == "schema_mismatch" and run["exitCode"] == 3
    assert [p["column"] for p in run["error"]["problems"]] == ["source_host"]
    assert (tmp_path / "out" / "run.json").exists(), "a failed run's directory is kept for diagnosis"
