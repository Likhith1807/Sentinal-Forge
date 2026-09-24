"""Rule execution engines behind one call: `run_rule(spec, events, policy, out_dir) -> run.json dict`.

* ``spark``     - the real executor (`RunRule`, Spark), used whenever a JVM and the built classpath exist.
* ``reference`` - the independent pure-Python engine, used when there is no JVM (e.g. the slim dashboard
                  image) and in unit tests. It is labelled in every run record ("engine": "python-reference"),
                  so a reader always knows which executor produced a result. It reads JSONL only and holds
                  the dataset in memory: fine for demonstration-sized data, not for the scale benchmarks.

Both write the same files (alerts.jsonl / quarantine.jsonl / run.json), atomically, and both keep a failed
run's directory.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import spark_engine
from ..refengine import parse_micros, run_reference


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def spark_available() -> tuple[bool, str]:
    try:
        spark_engine.find_java()
        spark_engine.find_sbt() if not spark_engine.CLASSPATH_FILE.exists() else None
        return True, ""
    except spark_engine.EngineUnavailable as exc:
        return False, str(exc)


def _path_bytes(path: Path) -> int:
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def choose(preference: str = "auto", events_path: Path | None = None, reference_max_bytes: int = 5_000_000) -> str:
    """`auto` picks the engine by what it costs. Spark starts a fresh JVM per run (about 18 s on the 725-event demo dataset, see
    docs/benchmarks.md) while the reference engine answers in tens of milliseconds, and the two are compared for equality by the
    differential suite - so a small JSONL dataset runs on the reference engine, and anything larger (or Parquet) on Spark when a
    JVM is available. Every run record says which engine produced it."""
    if preference in ("spark", "reference"):
        return preference
    small_jsonl = events_path is not None and Path(events_path).is_file() and Path(events_path).suffix in (".jsonl", ".json", ".ndjson")         and _path_bytes(events_path) <= reference_max_bytes
    if small_jsonl:
        return "reference"
    return "spark" if spark_available()[0] else "reference"


def _read_policy(path: Path | None) -> list[dict]:
    if not path:
        return []
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return doc["records"] if isinstance(doc, dict) and "records" in doc else doc


def run_reference_engine(spec_path: Path, events_path: Path, out_dir: Path, policy_path: Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.time()
    run: dict = {"runId": out_dir.name, "engine": "python-reference", "startedAt": started,
                 "input": {"events": str(events_path), "policy": str(policy_path) if policy_path else None, "spec": str(spec_path)}}
    try:
        compiled = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        run.update(behaviourId=compiled["behaviourId"], ruleHash=compiled.get("ruleHash"), compilerVersion=compiled.get("compilerVersion"))
        events = [json.loads(line) for line in Path(events_path).read_text(encoding="utf-8").splitlines() if line.strip()]
        policy = _read_policy(policy_path)
        if compiled["recipe"] == "PolicyCompare" and policy_path is None:
            raise ValueError("this rule joins a policy reference; a policy file is required")
        cols = {k for e in events for k in e}
        need = _needed_columns(compiled)
        missing = sorted(need - cols)
        if missing:
            run.update(status="failed", error={"kind": "schema_mismatch", "message": "event data cannot evaluate this rule: " + "; ".join(f"{c}: missing" for c in missing),
                                              "problems": [{"column": c, "problem": "missing"} for c in missing]})
            return _finish(run, out_dir, t0, 3)
        res = run_reference(compiled, events, policy)
        alerts = []
        for a in res.alerts:
            a = dict(a)
            a["detectedAtMicros"] = parse_micros(a["detectedAt"])
            if "evidence" in a:
                by_id = {e["event_id"]: e for e in events}
                a["evidence"] = [{"tsMicros": parse_micros(by_id[i]["timestamp"]), "eventId": i, "timestamp": by_id[i]["timestamp"],
                                  "value": _evidence_value(compiled, by_id[i])} for i in a["evidence"] if i in by_id]
            alerts.append(a)
        _atomic(out_dir / "alerts.jsonl", "".join(json.dumps(a) + "\n" for a in alerts))
        by_status: dict = {}
        for r in (res.all_results or res.alerts):
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        run["counts"] = {**{k: v for k, v in res.stats.items()}, "resultsByStatus": by_status,
                         "alertsWritten": len(alerts), "alertsTruncated": False}
        _atomic(out_dir / "quarantine.jsonl", "".join(json.dumps({"event_id": i, "reason": r}) + "\n" for i, r in res.quarantine[:1000]))
        run["status"] = "completed"
        return _finish(run, out_dir, t0, 0)
    except Exception as exc:  # noqa: BLE001 - a failed run is a result; its directory is kept
        run.update(status="failed", error={"kind": type(exc).__name__, "message": str(exc)})
        return _finish(run, out_dir, t0, 1)


def _needed_columns(compiled: dict) -> set:
    cols = {"event_id", "timestamp", "event_type"}
    if compiled["recipe"] == "SequenceThenTrigger":
        cols.add(compiled["groupingKey"])
    elif compiled["recipe"] == "DistinctCountWithinWindow":
        cols |= {compiled["groupingKey"], compiled["distinctField"]}
    else:
        cols |= {"account_id", compiled["logField"]}
    return cols


def _evidence_value(compiled: dict, event: dict):
    if compiled["recipe"] == "SequenceThenTrigger":
        return event.get("event_type")
    return event.get(compiled["distinctField"])


def _finish(run: dict, out_dir: Path, t0: float, code: int) -> dict:
    run["finishedAt"] = datetime.now(timezone.utc).isoformat()
    run["elapsedSeconds"] = round(time.time() - t0, 3)
    run["javaVersion"] = None
    _atomic(out_dir / "run.json", json.dumps(run, indent=2))
    run["exitCode"] = code
    return run


def run_rule(engine: str, spec_path: Path, events_path: Path, out_dir: Path, policy_path: Path | None = None,
             heap: str = "2g", timeout: float = 900) -> dict:
    if engine == "spark":
        return spark_engine.run_rule(spec_path, events_path, out_dir, policy_path, heap=heap, timeout=timeout)
    return run_reference_engine(spec_path, events_path, out_dir, policy_path)
