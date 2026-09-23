"""Batch/streaming agreement under the documented lateness policy, for all five behaviours.

    python scripts/verify/streaming_agreement.py --cases 40 --seed 5

For each behaviour the same adversarial scenarios used by the differential test are written as many small
files in a shuffled ARRIVAL order (so keys see out-of-order events), then processed by the streaming query.

Two regimes per behaviour:
  * generous lateness (nothing may be reported late): the alerts must equal the reference engine's over ALL events;
  * tight lateness (some events ARE reported late): the alerts must equal the reference engine's over the input
    MINUS exactly the events the stream reported as `late` - agreement is defined against the documented policy,
    and every late record must be justified (older than the key's finalised time).

Also compared: quarantine counts by reason, and that duplicates are reported rather than counted twice.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from differential import spec_for  # noqa: E402
from scenarios import US, gen_scenarios  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402
from sentinelforge import streaming  # noqa: E402
from sentinelforge.compile import compile_spec  # noqa: E402
from sentinelforge.refengine import parse_micros, run_reference  # noqa: E402


def write_chunks(events: list[dict], source: Path, chunk: int, rng: random.Random, heartbeat_after_s: int) -> int:
    rows = list(events)
    rng.shuffle(rows)
    source.mkdir(parents=True, exist_ok=True)
    n = 0
    for i in range(0, len(rows), chunk):
        (source / f"in-{n:05d}.json").write_text("\n".join(json.dumps(r) for r in rows[i:i + chunk]) + "\n", encoding="utf-8")
        n += 1
    stamps = [m for m in (parse_micros(e["timestamp"]) for e in events) if m is not None]
    hb_t = max(stamps) + heartbeat_after_s * US
    secs, us = divmod(hb_t, US)
    import datetime as dt
    ts = dt.datetime.fromtimestamp(secs, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"
    hb = {"event_id": "hb-1", "timestamp": ts, "account_id": "__flush__", "event_type": "__flush__", "source_host": "__flush__",
          "source_ip": None, "auth_method": "password", "mfa_used": False, "session_id": None}
    (source / f"in-{n:05d}.json").write_text(json.dumps(hb) + "\n", encoding="utf-8")
    return n + 1


def alert_keys(alerts: list[dict], policy: bool) -> dict:
    return {a["triggeringEventId"]: a for a in alerts}


def compare(ref_alerts: list, got: list, windowed: bool) -> list[str]:
    r = {a["triggeringEventId"]: a for a in ref_alerts}
    g = {a["triggeringEventId"]: a for a in got}
    problems = []
    if set(r) != set(g):
        problems.append(f"alert set differs: only-ref={sorted(set(r) - set(g))[:5]} only-stream={sorted(set(g) - set(r))[:5]}")
    for k in sorted(set(r) & set(g)):
        keys = ["groupKey", "detectedAt", "windowStart", "matchedCount"] if windowed else ["groupKey", "detectedAt", "status", "reason", "observedValue", "expectedValue", "policyVersion"]
        for f in keys:
            if r[k].get(f) != g[k].get(f):
                problems.append(f"{k}.{f}: ref={r[k].get(f)!r} stream={g[k].get(f)!r}")
        if windowed and r[k]["evidence"] != [e["eventId"] for e in g[k].get("evidence", [])]:
            problems.append(f"{k}.evidence differs")
    return problems


def run_regime(bid: str, cases: int, seed: int, lateness_s: int, work: Path, label: str) -> dict:
    beh = B.BEHAVIOURS[bid]
    windowed = beh.recipe != B.POLICY_COMPARE
    res = {"behaviour": bid, "regime": label, "latenessSeconds": lateness_s, "scenarios": 0, "problems": [], "late": 0, "duplicates": 0, "alerts": 0}
    for (w, t), scs in gen_scenarios(bid, cases, seed, streaming=True).items():
        compiled = compile_spec(spec_for(beh, w, t))
        d = work / f"{bid}-{label}-{w}-{t}"
        (d).mkdir(parents=True)
        spec_p = d / "spec.json"
        spec_p.write_text(json.dumps(compiled), encoding="utf-8")
        events = [e for s in scs for e in s.events]
        rng = random.Random(seed * 7919 + w)
        expiry = lateness_s + 3 * 24 * 3600                # >= window + lateness by a wide margin
        write_chunks(events, d / "src", chunk=max(20, len(events) // 12), rng=rng, heartbeat_after_s=expiry + lateness_s + 3600)
        pol_p = None
        if not windowed:
            pol_p = d / "policy.json"
            pol_p.write_text(json.dumps({"records": [p for s in scs for p in s.policy]}), encoding="utf-8")
        code = streaming.run_to_completion(spec_p, d / "src", d / "out", d / "ckpt", pol_p, lateness_s=lateness_s, expiry_s=expiry,
                                           available_now=True, max_files=3, log=d / "stream.log")
        if code != 0:
            res["problems"].append({"config": [w, t], "engineFailure": (d / "stream.log").read_text(errors="replace")[-800:]})
            continue
        got = streaming.read_kind(d / "out", "alert" if windowed else "policy")
        late_ids = {x["triggeringEventId"] for x in streaming.read_kind(d / "out", "late")}
        dup_n = len(streaming.read_kind(d / "out", "duplicate"))
        quar = Counter(x["reason"] for x in streaming.read_kind(d / "out", "quarantine"))
        by_sc: dict = {}
        for a in got:
            by_sc.setdefault(a["groupKey"].split("-")[0], []).append(a)
        res["late"] += len(late_ids)
        res["duplicates"] += dup_n
        ref_quar: Counter = Counter()
        for s in scs:
            kept = [e for e in s.events if e["event_id"] not in late_ids]
            ref = run_reference(compiled, kept, s.policy)
            for r_, n in ref.stats["quarantineByReason"].items():
                ref_quar[r_] += n
            pr = compare(ref.alerts, by_sc.get(s.id, []), windowed)
            if pr and len(res["problems"]) < 12:
                res["problems"].append({"scenario": s.id, "config": [w, t], "tags": s.tags, "diffs": pr[:5]})
        res["scenarios"] += len(scs)
        res["alerts"] += len(got)
        if quar != ref_quar:
            res["problems"].append({"config": [w, t], "quarantine": {"ref": dict(ref_quar), "stream": dict(quar)}})
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=30)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--behaviours", default="all")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    ids = B.BEHAVIOUR_IDS if a.behaviours == "all" else a.behaviours.split(",")
    work = Path(tempfile.mkdtemp(prefix="sf-stream-"))
    started, results = time.time(), []
    for bid in ids:
        regimes = [(30 * 24 * 3600, "generous")] + ([(120, "tight")] if B.BEHAVIOURS[bid].windowed else [])
        for lateness, label in regimes:
            r = run_regime(bid, a.cases, a.seed, lateness, work, label)
            results.append(r)
            print(f"{'AGREE' if not r['problems'] else 'DISAGREE':9s} {bid:40s} {label:9s} scenarios={r['scenarios']:3d} alerts={r['alerts']:4d} late={r['late']:3d} dup-records={r['duplicates']:3d}")
            for p in r["problems"][:2]:
                print("   ", json.dumps(p)[:300])
    bad = sum(1 for r in results if r["problems"])
    summary = {"seed": a.seed, "casesPerBehaviour": a.cases, "seconds": round(time.time() - started, 1), "regimesWithDisagreement": bad, "results": results}
    if a.out:
        a.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    print(f"\n{len(results)} regimes, {bad} with disagreement, {summary['seconds']}s")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
