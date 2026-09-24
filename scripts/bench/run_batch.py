"""Batch benchmark driver.

    # small, reproducible (~2 min): generates ~1.3M events, 30 repetitions per behaviour
    python scripts/bench/run_batch.py --dataset data/generated/bench_small --generate --reps 30
    # large (uses an existing generated dataset), fewer repetitions -> only medians / ranges are reported
    python scripts/bench/run_batch.py --dataset data/generated/scale_1gb --reps 5 --heap 6g --name scale_1gb

Writes experiments/results/benchmarks/<name>.json (raw observations from the JVM) and <name>.summary.json (hardware +
sample-size-aware statistics). Read docs/benchmarks.md before quoting anything from them.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, describe, hardware  # noqa: E402

from sentinelforge import spark_engine as se  # noqa: E402

OUT = REPO / "experiments" / "results" / "benchmarks"


def generate(dataset: Path, accounts: int, days: int, incidents: int) -> None:
    if (dataset / "manifest.json").exists():
        print(f"{dataset} exists; reusing")
        return
    t = time.time()
    subprocess.run([sys.executable, "-m", "scripts.datagen.generate", "--out", str(dataset), "--accounts", str(accounts), "--days", str(days),
                    "--incidents-per-behaviour", str(incidents), "--seed", "1"], cwd=REPO, check=True)
    print(f"generated in {time.time() - t:.0f}s")


def run_jvm(dataset: Path, reps: int, warmup: int, heap: str, master: str, out: Path, cache: bool, shuffle: int, behaviours: str | None) -> int:
    java = se.find_java()
    cp = se.ensure_built().read_text(encoding="utf-8").strip()
    cmd = se.jvm_command(java, heap) + ["-cp", cp, "sentinelforge.compiler.Benchmark", "--dataset", str(dataset), "--reps", str(reps), "--warmup", str(warmup),
                                        "--master", master, "--cache", str(cache).lower(), "--shuffle-partitions", str(shuffle), "--out", str(out)]
    if behaviours:
        cmd += ["--behaviours", behaviours]
    p = se.popen_tree(cmd, cwd=REPO, env=se.child_env())
    try:
        return p.wait()
    finally:
        se.kill_tree(p)


def summarise(raw: dict, hw: dict, name: str, wall_s: float) -> dict:
    by = defaultdict(lambda: {"warm": [], "warmup": [], "failed": 0, "attempted": 0, "heap": [], "gc": [], "rows": set()})
    for o in raw["observations"]:
        b = by[o["behaviourId"]]
        b["attempted"] += 1
        if o["failed"]:
            b["failed"] += 1
            continue
        (b["warmup"] if o["warmup"] else b["warm"]).append(o["seconds"])
        b["heap"].append(o["peakHeapMB"]), b["gc"].append(o["gcSeconds"]), b["rows"].add(o["resultRows"])
    events = raw["datasetShape"]["events"]
    per = {}
    for bid, b in by.items():
        d = describe(b["warm"])
        per[bid] = {"warmSeconds": d, "warmupSeconds": b["warmup"], "attempted": b["attempted"], "failed": b["failed"],
                    "failureRate": b["failed"] / b["attempted"] if b["attempted"] else None,
                    "eventsPerSecondAtMedian": events / d["median"] if d.get("median") else None,
                    "peakHeapMB": describe(b["heap"]), "gcSeconds": describe(b["gc"]),
                    "resultRowsConsistentAcrossRuns": len(b["rows"]) <= 1}
    return {"name": name, "hardware": hw, "jvm": raw["environment"], "mode": raw["mode"], "dataset": raw["dataset"], "datasetShape": raw["datasetShape"],
            "coldStart": raw["coldStart"], "repetitionsPerBehaviour": raw["repetitionsPerBehaviour"], "warmupPerBehaviour": raw["warmupPerBehaviour"],
            "driverWallSeconds": round(wall_s, 1), "perBehaviour": per,
            "readingGuide": "Statistics use only warm, non-failed repetitions. Percentiles appear only when n supports them (p95 needs n>=20, "
                            "p99 n>=100); otherwise use median / IQR / range. Throughput is events per second at the median and includes Parquet I/O "
                            "when mode says so. Single machine: this is local Spark, not evidence of horizontal scalability."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--generate", action="store_true", help="generate the small reproducible dataset first")
    ap.add_argument("--accounts", type=int, default=60000)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--incidents", type=int, default=100)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--heap", default="4g")
    ap.add_argument("--master", default="local[*]")
    ap.add_argument("--cache", action="store_true")
    ap.add_argument("--shuffle-partitions", type=int, default=64)
    ap.add_argument("--behaviours")
    ap.add_argument("--name")
    a = ap.parse_args(argv)
    name = a.name or a.dataset.name + ("_cached" if a.cache else "")
    OUT.mkdir(parents=True, exist_ok=True)
    if a.generate:
        generate(a.dataset, a.accounts, a.days, a.incidents)
    raw_path = OUT / f"{name}.json"
    t = time.time()
    code = run_jvm(a.dataset, a.reps, a.warmup, a.heap, a.master, raw_path, a.cache, a.shuffle_partitions, a.behaviours)
    if code != 0 or not raw_path.exists():
        print("benchmark JVM failed with exit code", code)
        return 1
    summary = summarise(json.loads(raw_path.read_text(encoding="utf-8")), hardware(), name, time.time() - t)
    (OUT / f"{name}.summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    ev = summary["datasetShape"]["events"]
    print(f"\n{name}: {ev:,} events on {summary['hardware']['cpuModel']} ({summary['jvm']['availableProcessors']} cpus, heap {summary['jvm']['maxHeapMB']} MB); "
          f"JVM->session {summary['coldStart']['jvmToSessionReadySeconds']:.1f}s")
    for bid, p in summary["perBehaviour"].items():
        w = p["warmSeconds"]
        print(f"  {bid:40s} n={w['n']:3d} median={w.get('median', float('nan')):6.2f}s  "
              f"range=[{w.get('min', float('nan')):.2f}, {w.get('max', float('nan')):.2f}]  "
              f"p95={'n/a' if w.get('p95') is None else round(w['p95'], 2)}  failed={p['failed']}/{p['attempted']}  peak heap {p['peakHeapMB'].get('max', 0):.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
