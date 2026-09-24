"""Streaming stress: high key cardinality and a skewed hot key.

    python scripts/bench/streaming_stress.py --out experiments/results/benchmarks/streaming_stress.json

Two shapes, each run to completion (available-now) on one local JVM and each checked against the exact alert count it must produce:

  high-cardinality   many distinct accounts, most with a partial pattern that must NOT alert, a known number with a full pattern
  hot-key skew       one source host failing against very many distinct accounts inside one window, plus ordinary hosts

The hot key is the honest limit of the design: a windowed key's retained history is proportional to the events inside its window
(O(H) for a hot key), and that key is processed by one task. The result records the state size Spark reports so the limit is visible,
not asserted. There is no distributed measurement here: one machine, `local[*]`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, hardware  # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "verify"))
from differential import spec_for  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402
from sentinelforge import streaming  # noqa: E402
from sentinelforge.compile import compile_spec  # noqa: E402

BASE = dt.datetime(2026, 1, 5, 9, 0, 0, tzinfo=dt.timezone.utc)
LATENESS, WINDOW = 60, 3600
EXPIRY = WINDOW + LATENESS + 600


def ts(sec: float) -> str:
    return (BASE + dt.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ev(eid: str, t: float, account: str, etype: str, host: str) -> str:
    return json.dumps({"event_id": eid, "timestamp": ts(t), "account_id": account, "event_type": etype, "source_host": host, "source_ip": None,
                       "auth_method": "password", "mfa_used": False, "session_id": None})


def write_files(src: Path, lines: list[str], per_file: int) -> int:
    n = 0
    for i in range(0, len(lines), per_file):
        (src / f"in-{i // per_file:06d}.json").write_text("\n".join(lines[i:i + per_file]) + "\n", encoding="utf-8")
        n += 1
    return n


def heartbeat(t: float) -> str:
    return ev("hb", t + EXPIRY + LATENESS + 3600, "__flush__", "__flush__", "__flush__")


def run(name: str, bid: str, threshold: int, lines: list[str], expected: int, last_t: float, work: Path, heap: str) -> dict:
    d = work / name
    src, out, ckpt = d / "src", d / "out", d / "ckpt"
    for p in (src, out, ckpt):
        p.mkdir(parents=True)
    compiled = compile_spec(spec_for(B.BEHAVIOURS[bid], WINDOW, threshold))
    (d / "spec.json").write_text(json.dumps(compiled), encoding="utf-8")
    files = write_files(src, lines, 50_000)
    (src / "zz-heartbeat.json").write_text(heartbeat(last_t) + "\n", encoding="utf-8")
    t = time.time()
    rc = streaming.run_to_completion(d / "spec.json", src, out, ckpt, None, timeout=1800, lateness_s=LATENESS, expiry_s=EXPIRY, trigger_s=1,
                                     available_now=True, max_files=4, heap=heap)
    elapsed = time.time() - t
    alerts = streaming.read_kind(out, "alert")
    state_bytes, rows_total, dropped, batches = 0, 0, 0, 0
    for f in (out / "progress").glob("batch-*.json"):
        p = json.loads(f.read_text(encoding="utf-8"))
        batches += 1
        for o in p.get("stateOperators", []):
            state_bytes = max(state_bytes, o.get("memoryUsedBytes", 0))
            rows_total = max(rows_total, o.get("numRowsTotal", 0))
            dropped += o.get("numRowsDroppedByWatermark", 0)
    return {"name": name, "behaviourId": bid, "events": len(lines), "inputFiles": files, "heapSetting": heap, "exitCode": rc,
            "elapsedSecondsIncludingJvmStart": round(elapsed, 1), "eventsPerSecondEndToEnd": round(len(lines) / elapsed),
            "expectedAlerts": expected, "alerts": len(alerts), "alertCountMatches": len(alerts) == expected,
            "duplicateAlertIds": len(alerts) - len({a["triggeringEventId"] for a in alerts}),
            "lateRecords": len(streaming.read_kind(out, "late")), "duplicateRecords": len(streaming.read_kind(out, "duplicate")),
            "peakStateMemoryMB": round(state_bytes / 2**20, 1), "peakStateRows": rows_total, "rowsDroppedByWatermark": dropped, "microBatches": batches}


def show(r: dict) -> None:
    print("  ", {k: r[k] for k in ("elapsedSecondsIncludingJvmStart", "eventsPerSecondEndToEnd", "alerts", "expectedAlerts", "peakStateMemoryMB")}, flush=True)


def high_cardinality(a, work: Path) -> dict:
    """`accounts` accounts with 2 failures (threshold 3 -> no alert) and `full_incidents` with 3 failures then a success."""
    lines: list[str] = []
    for i in range(a.accounts):
        k = f"acct-{i:07d}"
        lines += [ev(f"p{i}-0", 10 + (i % 3000), k, "login_failure", "h1"), ev(f"p{i}-1", 12 + (i % 3000), k, "login_failure", "h1")]
    for i in range(a.full_incidents):
        k = f"full-{i:06d}"
        t = 20 + (i % 3000)
        lines += [ev(f"f{i}-{j}", t + j, k, "login_failure", "h1") for j in range(3)] + [ev(f"f{i}-s", t + 4, k, "login_success", "h1")]
    print(f"high-cardinality: {len(lines):,} events, {a.accounts + a.full_incidents:,} keys", flush=True)
    r = run("high-cardinality", "repeated-failed-login-then-success", 3, lines, a.full_incidents, 4000, work, a.heap)
    show(r)
    return r


def hot_key(a, work: Path) -> dict:
    """One host, `hot_accounts` distinct accounts failing inside one window (threshold 5 -> one rising-edge alert), plus ordinary hosts."""
    lines = [ev(f"hot-{i}", 100 + (i % 3000), f"victim-{i:07d}", "login_failure", "hot-host") for i in range(a.hot_accounts)]
    for i in range(a.full_incidents):
        lines += [ev(f"n{i}-{j}", 50 + (i % 3000) + j, f"n{i}-acct-{j}", "login_failure", f"host-{i:05d}") for j in range(5)]
    print(f"hot-key: {len(lines):,} events, one key holds {a.hot_accounts:,}", flush=True)
    r = run(f"hot-key-skew-{a.hot_accounts}", "password-spray-across-accounts", 5, lines, a.full_incidents + 1, 4000, work, a.heap)
    show(r)
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", type=int, default=200_000)
    ap.add_argument("--full-incidents", type=int, default=1_000)
    ap.add_argument("--hot-accounts", type=int, default=100_000)
    ap.add_argument("--heap", default="3g")
    ap.add_argument("--only", choices=("high-cardinality", "hot-key"), help="run just one shape (used for the hot-key sweep)")
    ap.add_argument("--out", default=str(REPO / "experiments" / "results" / "benchmarks" / "streaming_stress.json"))
    a = ap.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="sf-stress-", dir=REPO / "data" / "generated" / "spark_tmp"))
    results = []
    try:
        if a.only in (None, "high-cardinality"):
            results.append(high_cardinality(a, work))
        if a.only in (None, "hot-key"):
            results.append(hot_key(a, work))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    doc = {"kind": "streaming-stress", "hardware": hardware(), "method": __doc__.strip(), "results": results,
           "limits": ["single machine, local[*] Spark", "synthetic events", "a hot key's state is O(events in its window) and runs on one task"]}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
