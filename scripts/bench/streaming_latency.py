"""Streaming latency: how long after an alert becomes decidable does it appear in the output?

    python scripts/bench/streaming_latency.py --incidents 100 --lateness 0,5,30 --out experiments/results/benchmarks/streaming_latency.json

Method (a single local JVM, one machine, `local[*]`, trigger interval 1 s):
  * a driver writes one immutable JSON-lines file per step (write to a temp name, then atomic rename) and records the wall-clock
    time of the rename;
  * an incident is 3 failed logins then a success for a fresh account (windowed recipe) or `k` failures across distinct accounts
    from one host (distinct recipe); incidents are spaced `--gap` seconds apart;
  * an alert is DECIDABLE when the key has seen an event at least `lateness` newer than the triggering event (the engine finalises
    in timestamp order, see StreamingEngine). The driver therefore sends, per incident, one "closing" event for the same key
    with timestamp = trigger + lateness (for lateness 0 the trigger itself is decidable). Latency = alert file modification time
    minus the rename time of the file holding the decidable-making event.

What this measures: the engine's own processing + micro-batch + commit latency once the delay policy is satisfied. It does NOT
include the `lateness` delay itself (that is a configured policy: an alert cannot be released before an event `lateness`
newer arrives), source transport, or a cluster. Percentiles are reported only where n supports them (n >= 20 -> p95, >= 100 -> p99).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, describe, hardware  # noqa: E402

from sentinelforge import streaming  # noqa: E402
from sentinelforge.compile import compile_spec  # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "verify"))
from differential import spec_for  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402

BASE = dt.datetime(2026, 1, 5, 9, 0, 0, tzinfo=dt.timezone.utc)


def ts(seconds: float) -> str:
    return (BASE + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ev(eid: str, t: float, account: str, etype: str, host: str) -> dict:
    return {"event_id": eid, "timestamp": ts(t), "account_id": account, "event_type": etype, "source_host": host, "source_ip": None,
            "auth_method": "password", "mfa_used": False, "session_id": None}


def put(src: Path, name: str, rows: list[dict]) -> float:
    tmp = src / f".{name}.tmp"
    tmp.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    os.replace(tmp, src / name)
    return time.time()


def run_config(bid: str, lateness: int, incidents: int, gap: float, work: Path, threshold: int, window: int) -> dict:
    beh = B.BEHAVIOURS[bid]
    compiled = compile_spec(spec_for(beh, window, threshold))
    d = work / f"{bid}-L{lateness}"
    src, out, ckpt = d / "src", d / "out", d / "ckpt"
    for p in (src, out, ckpt):
        p.mkdir(parents=True)
    (d / "spec.json").write_text(json.dumps(compiled), encoding="utf-8")
    expiry = window + lateness + 600
    proc = streaming.start(d / "spec.json", src, out, ckpt, None, log=d / "run.log", lateness_s=lateness, expiry_s=expiry, trigger_s=1,
                           available_now=False, max_files=50, idle_exit_s=0, heap="1g")
    decidable_at: dict[str, float] = {}
    try:
        # Do not start the clock until the query has actually processed something: a fixed sleep let the first few incidents queue up
        # behind JVM start-up and made them look like slow alerts. The startup time is reported separately.
        t_start = time.time()
        put(src, "in-000000-warmup.json", [ev("warmup-0", 0, "warmup", "login_success", "warmup-host")])
        deadline = time.time() + 180
        while time.time() < deadline and not any(json.loads(f.read_text(encoding="utf-8")).get("numInputRows", 0) > 0
                                                 for f in (out / "progress").glob("batch-*.json")):
            time.sleep(0.25)
        startup_s = time.time() - t_start
        t0 = time.time()
        for i in range(incidents):
            base = i * (window * 2 + lateness + 10)           # separate incidents in event time
            key = f"acct-{i:05d}" if beh.recipe == B.SEQUENCE_THEN_TRIGGER else f"host-{i:05d}"
            if beh.recipe == B.SEQUENCE_THEN_TRIGGER:
                rows = [ev(f"i{i}-f{k}", base + k, key, "login_failure", "h1") for k in range(threshold)] + \
                       [ev(f"i{i}-s", base + threshold + 1, key, "login_success", "h1")]
                trig_id, trig_t = f"i{i}-s", base + threshold + 1
            else:
                rows = [ev(f"i{i}-f{k}", base + k, f"acct-{i:05d}-{k}", "login_failure", key) for k in range(threshold)]
                trig_id, trig_t = f"i{i}-f{threshold - 1}", base + threshold - 1
            arrival = put(src, f"in-{i:06d}-a.json", rows)
            if lateness == 0:
                decidable_at[trig_id] = arrival
            else:
                close = [ev(f"i{i}-close", trig_t + lateness, key if beh.recipe == B.SEQUENCE_THEN_TRIGGER else f"acct-{i:05d}-x",
                            "login_failure", "h1" if beh.recipe == B.SEQUENCE_THEN_TRIGGER else key)]
                decidable_at[trig_id] = put(src, f"in-{i:06d}-b.json", close)
            time.sleep(gap)
        deadline = time.time() + 60
        while time.time() < deadline:                       # let the last batches commit
            if len(streaming.read_kind(out, "alert")) >= incidents:
                break
            time.sleep(1)
        elapsed = time.time() - t0
    finally:
        streaming.kill(proc)
    lat, missing = [], 0
    seen = set()
    for f in sorted((out / "alert").glob("batch-*.jsonl")):
        m = f.stat().st_mtime
        for line in f.read_text(encoding="utf-8").splitlines():
            a = json.loads(line)
            tid = a["triggeringEventId"]
            if tid in decidable_at and tid not in seen:
                seen.add(tid)
                lat.append(m - decidable_at[tid])
    missing = incidents - len(seen)
    return {"behaviourId": bid, "latenessSeconds": lateness, "windowSeconds": window, "threshold": threshold, "incidentsSent": incidents,
            "alertsMatched": len(seen), "missingAlerts": missing, "gapSeconds": gap, "triggerIntervalSeconds": 1,
            "startupSecondsBeforeFirstBatch": round(startup_s, 1), "latencySeconds": describe(lat), "rawLatencySeconds": [round(x, 3) for x in lat], "driverElapsedSeconds": round(elapsed, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--incidents", type=int, default=100)
    ap.add_argument("--lateness", default="0,5,30")
    ap.add_argument("--gap", type=float, default=2.0)
    ap.add_argument("--behaviours", default="repeated-failed-login-then-success,password-spray-across-accounts")
    ap.add_argument("--out", default=str(REPO / "experiments" / "results" / "benchmarks" / "streaming_latency.json"))
    a = ap.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="sf-lat-", dir=REPO / "data" / "generated" / "spark_tmp"))
    results = []
    try:
        for bid in a.behaviours.split(","):
            for L in [int(x) for x in a.lateness.split(",")]:
                print(f"{bid} lateness={L}s ...", flush=True)
                r = run_config(bid, L, a.incidents, a.gap, work, threshold=3, window=60)
                s = r["latencySeconds"]
                print(f"  matched {r['alertsMatched']}/{r['incidentsSent']}  median {s.get('median', float('nan')):.2f}s  max {s.get('max', float('nan')):.2f}s", flush=True)
                results.append(r)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    doc = {"kind": "streaming-latency", "hardware": hardware(), "method": __doc__.strip(), "results": results,
           "limits": ["single machine, local[*] Spark, one query at a time", "synthetic events, one key per incident", "excludes the configured lateness delay",
                  "run under scripts/bench/keep_awake.py so the laptop cannot enter standby mid-run"]}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
