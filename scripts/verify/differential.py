"""Differential test: the Spark executor vs the independent reference engine, on adversarial scenarios.

    python scripts/verify/differential.py --cases 100 --seed 7                 # all five behaviours
    python scripts/verify/differential.py --behaviours password-spray-across-accounts --cases 300

For every scenario the two engines must agree on: the SET of alerts, each alert's group, instant, window
start, matched count and supporting-event evidence (ordered), each policy result's status / reason /
observed / expected value, and - across the whole dataset - the counts of quarantined rows (by reason),
dropped duplicates and evaluated events.

Read the limits of this test before quoting it: two implementations agreeing shows they match EACH OTHER.
A defect both share (a misunderstanding of the semantics) passes here. That is why tests/core also holds
hand-computed golden scenarios and metamorphic properties that need no second implementation.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenarios import gen_scenarios  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402
from sentinelforge import spark_engine  # noqa: E402
from sentinelforge.compile import compile_spec  # noqa: E402
from sentinelforge.refengine import run_reference  # noqa: E402

_SC = re.compile(r"^(s\d+)-")


def spec_for(beh: B.Behaviour, window_s: int, threshold: int) -> dict:
    spec = {"behaviourId": beh.id, "requiredFields": list(beh.log_fields), "policyFields": list(beh.policy_fields)}
    if beh.recipe != B.POLICY_COMPARE:
        spec["threshold"] = {next(iter(beh.threshold_aliases)): threshold}
        spec["timeWindow"] = {"amount": window_s, "unit": "seconds"}
    return spec


def compare(ref_alerts: list, got_alerts: list, windowed: bool) -> list[str]:
    problems = []
    r = {a["triggeringEventId"]: a for a in ref_alerts}
    g = {a["triggeringEventId"]: a for a in got_alerts}
    if set(r) != set(g):
        problems.append(f"alert set differs: only-ref={sorted(set(r) - set(g))} only-spark={sorted(set(g) - set(r))}")
    for k in sorted(set(r) & set(g)):
        a, b = r[k], g[k]
        keys = ["groupKey", "detectedAt", "status"] + (["windowStart", "matchedCount"] if windowed else ["reason", "observedValue", "expectedValue"])
        for f in keys:
            if a.get(f) != b.get(f):
                problems.append(f"{k}.{f}: ref={a.get(f)!r} spark={b.get(f)!r}")
        if windowed:
            ev = [e["eventId"] for e in b.get("evidence", [])]
            if a["evidence"] != ev:
                problems.append(f"{k}.evidence: ref={a['evidence']} spark={ev}")
    return problems


def run_behaviour(behaviour_id: str, cases: int, seed: int, workdir: Path) -> dict:
    beh = B.BEHAVIOURS[behaviour_id]
    windowed = beh.recipe != B.POLICY_COMPARE
    result = {"behaviourId": behaviour_id, "configs": [], "scenarios": 0, "mismatchedScenarios": 0, "problems": []}
    for (w, t), scs in gen_scenarios(behaviour_id, cases, seed).items():
        compiled = compile_spec(spec_for(beh, w, t))
        run_dir = workdir / f"{behaviour_id}-w{w}-n{t}"
        run_dir.mkdir(parents=True, exist_ok=True)
        events = [e for s in scs for e in s.events]
        (run_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        (run_dir / "spec.json").write_text(json.dumps(compiled), encoding="utf-8")
        policy_path = None
        if not windowed:
            (run_dir / "policy.json").write_text(json.dumps({"records": [p for s in scs for p in s.policy]}), encoding="utf-8")
            policy_path = run_dir / "policy.json"
        t0 = time.time()
        run = spark_engine.run_rule(run_dir / "spec.json", run_dir / "events.jsonl", run_dir / "out", policy_path, heap="2g")
        if run.get("status") != "completed":
            result["problems"].append({"config": [w, t], "engineFailure": run.get("error")})
            continue
        by_sc: dict = defaultdict(list)
        for a in spark_engine.read_alerts(run_dir / "out"):
            m = _SC.match(a["groupKey"])
            by_sc[m.group(1) if m else "?"].append(a)
        ref_stats = defaultdict(int)
        ref_reason: dict = defaultdict(int)
        bad = 0
        for s in scs:
            ref = run_reference(compiled, s.events, s.policy)
            for k in ("eventsRead", "eventsEvaluated", "quarantined", "duplicatesDropped"):
                ref_stats[k] += ref.stats[k]
            for reason, n in ref.stats["quarantineByReason"].items():
                ref_reason[reason] += n
            problems = compare(ref.alerts, by_sc.get(s.id, []), windowed)
            if problems:
                bad += 1
                if len(result["problems"]) < 25:
                    result["problems"].append({"scenario": s.id, "config": [w, t], "tags": s.tags, "diffs": problems[:6]})
        counts = run["counts"]
        for k in ("eventsRead", "eventsEvaluated", "quarantined", "duplicatesDropped"):
            if counts[k] != ref_stats[k]:
                bad += 1
                result["problems"].append({"config": [w, t], "counter": k, "ref": ref_stats[k], "spark": counts[k]})
        if counts["quarantineByReason"] != dict(ref_reason):
            bad += 1
            result["problems"].append({"config": [w, t], "quarantineByReason": {"ref": dict(ref_reason), "spark": counts["quarantineByReason"]}})
        result["configs"].append({"window": w, "threshold": t, "scenarios": len(scs), "events": len(events),
                                  "alerts": sum(len(v) for v in by_sc.values()), "mismatches": bad,
                                  "seconds": round(time.time() - t0, 1)})
        result["scenarios"] += len(scs)
        result["mismatchedScenarios"] += bad
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=60, help="scenarios per behaviour")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--behaviours", default="all")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory (datasets, run outputs)")
    args = ap.parse_args(argv)
    ids = B.BEHAVIOUR_IDS if args.behaviours == "all" else args.behaviours.split(",")
    work = Path(tempfile.mkdtemp(prefix="sf-diff-"))
    started = time.time()
    results = []
    for bid in ids:
        r = run_behaviour(bid, args.cases, args.seed, work)
        results.append(r)
        status = "AGREE" if not r["problems"] else "DISAGREE"
        print(f"{status:9s} {bid:42s} scenarios={r['scenarios']:4d} mismatches={r['mismatchedScenarios']}")
        for p in r["problems"][:3]:
            print("   ", json.dumps(p)[:400])
    total = sum(r["scenarios"] for r in results)
    bad = sum(r["mismatchedScenarios"] for r in results)
    summary = {"seed": args.seed, "casesPerBehaviour": args.cases, "totalScenarios": total, "mismatched": bad,
               "engineFailures": sum(1 for r in results for p in r["problems"] if "engineFailure" in p),
               "seconds": round(time.time() - started, 1), "results": results}
    print(f"\n{total} scenarios, {bad} mismatches, {summary['seconds']}s")
    if args.out:
        args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if bad == 0 and summary["engineFailures"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
