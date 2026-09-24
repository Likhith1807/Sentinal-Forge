"""What an analyst waits for: the service's run job on the committed demonstration dataset, cold JVM per run versus the reference engine.

    python scripts/bench/service_latency.py --spark-reps 8 --reference-reps 30 --out experiments/results/benchmarks/service_latency.json

Each Spark run starts a fresh JVM (that is how the service isolates runs), so this is the *cold* per-run latency a person sees on a
small dataset; it is dominated by JVM and Spark session start-up, not by the data. The reference engine is pure Python and is used for
small datasets. Both are measured through `Workspace.submit_run` (job queue, run directory, result files, summary), on the same
725-event dataset. One machine, one process, no concurrent load.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, describe, hardware  # noqa: E402

from sentinelforge.service.settings import Settings  # noqa: E402
from sentinelforge.service.workflow import Workspace  # noqa: E402

REPORT = REPO / "data" / "demo" / "reports" / "01-brute-force-vpn.md"


def measure(engine: str, reps: int) -> dict:
    state = Path(tempfile.mkdtemp(prefix="sf-svc-", dir=REPO / "data" / "generated" / "spark_tmp"))
    ws = Workspace(Settings(state_dir=state, engine=engine))
    try:
        rep = ws.create_report(REPORT.read_text(encoding="utf-8"))
        sub = ws.submit_analysis(rep["id"])
        while ws.get_analysis(sub["analysisId"])["status"] not in ("ready", "needs_review", "rejected", "failed"):
            time.sleep(0.05)
        rv = ws.create_rule_version(sub["analysisId"], "bench")
        secs, failed, alerts = [], 0, set()
        for _ in range(reps):
            t = time.time()
            r = ws.submit_run(rv["id"])
            while True:
                run = ws.get_run(r["runId"])
                if run["state"] in ("completed", "failed"):
                    break
                time.sleep(0.02)
            if run["state"] == "failed":
                failed += 1
                continue
            secs.append(time.time() - t)
            alerts.add(ws.alerts(r["runId"])["total"])
        return {"engine": engine, "attempted": reps, "failed": failed, "seconds": describe(secs), "rawSeconds": [round(x, 3) for x in secs],
                "alertsPerRun": sorted(alerts)}
    finally:
        ws.shutdown()
        shutil.rmtree(state, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spark-reps", type=int, default=8)
    ap.add_argument("--reference-reps", type=int, default=30)
    ap.add_argument("--out", default=str(REPO / "experiments" / "results" / "benchmarks" / "service_latency.json"))
    a = ap.parse_args(argv)
    results = [measure("reference", a.reference_reps), measure("spark", a.spark_reps)]
    for r in results:
        s = r["seconds"]
        print(f"{r['engine']:10s} n={s.get('n')} median={s.get('median', float('nan')):.2f}s min={s.get('min', float('nan')):.2f} max={s.get('max', float('nan')):.2f} failed={r['failed']} alerts={r['alertsPerRun']}")
    doc = {"kind": "service-run-latency", "hardware": hardware(), "method": __doc__.strip(), "dataset": "data/demo (725 events)", "results": results,
           "limits": ["single machine, no concurrent load", "Spark run = fresh local JVM per run (cold)", "small dataset: measures overhead, not data-size scaling"]}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
