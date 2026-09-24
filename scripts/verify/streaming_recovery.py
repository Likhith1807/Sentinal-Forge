"""Restart-during-processing and duplicate-delivery tests for the streaming executor.

    python scripts/verify/streaming_recovery.py --kill-after 2,4,6 --out experiments/results/streaming_recovery.json

For each kill point: feed files one at a time to a running query, HARD-KILL the JVM (no graceful stop, no
final commit) after N micro-batches, deliver the remaining files while nothing is running, restart against the
SAME checkpoint, then compare with the reference engine over all events. Checked:

  * no alert is missing and none is duplicated (each triggering event appears exactly once in the output files),
  * output batch files are contiguous and a replayed batch overwrote its own file rather than adding a new one,
  * re-delivering every input file a second time (a collector retry) adds only `duplicate`/`late` records, never
    a second alert.

What this does NOT show, and the docs say so: recovery needs the checkpoint AND the output directory to survive
together; losing the checkpoint but not the output re-emits alerts; the source files must be immutable and
retained; two queries sharing one checkpoint are unsupported.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from differential import spec_for  # noqa: E402
from scenarios import gen_scenarios  # noqa: E402
from streaming_agreement import compare  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402
from sentinelforge import streaming  # noqa: E402
from sentinelforge.compile import compile_spec  # noqa: E402
from sentinelforge.refengine import parse_micros, run_reference  # noqa: E402

LATENESS, EXPIRY = 600, 3 * 86400 + 600


def heartbeat(events: list[dict]) -> dict:
    import datetime as dt
    t = max(m for m in (parse_micros(e["timestamp"]) for e in events) if m is not None) // 1_000_000 + EXPIRY + LATENESS + 3600
    ts = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"event_id": "hb-1", "timestamp": ts, "account_id": "__flush__", "event_type": "__flush__", "source_host": "__flush__",
            "source_ip": None, "auth_method": "password", "mfa_used": False, "session_id": None}


def wait_for_batches(out: Path, n: int, proc, timeout: float = 120) -> bool:
    end = time.time() + timeout
    while time.time() < end and proc.poll() is None:
        if streaming.batches_written(out, "alert") >= n:
            return True
        time.sleep(0.25)
    return False


def one_kill_point(bid: str, cases: int, seed: int, kill_after: int, work: Path) -> dict:
    beh = B.BEHAVIOURS[bid]
    (w, t), scs = next(iter(gen_scenarios(bid, cases, seed, streaming=True).items()))
    compiled = compile_spec(spec_for(beh, w, t))
    d = work / f"kill{kill_after}"
    src, out, ckpt = d / "src", d / "out", d / "ckpt"
    for p in (src, out, ckpt):
        p.mkdir(parents=True)
    spec_p = d / "spec.json"
    spec_p.write_text(json.dumps(compiled), encoding="utf-8")
    events = [e for s in scs for e in s.events]
    random.Random(seed).shuffle(events)
    size = max(15, len(events) // 14)
    files = [events[i:i + size] for i in range(0, len(events), size)]

    def put(name: str, rows: list[dict]) -> None:
        (src / name).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    common = dict(lateness_s=LATENESS, expiry_s=EXPIRY, trigger_s=1, available_now=False, max_files=1)
    p1 = streaming.start(spec_p, src, out, ckpt, None, log=d / "run1.log", idle_exit_s=0, **common)
    fed = 0
    for i, rows in enumerate(files):
        put(f"in-{i:05d}.json", rows)
        if i == 1:
            put("mid-retry-00000.json", files[0])                # collector retry while the keys' state is still retained
        fed += 1
        if wait_for_batches(out, kill_after, p1, timeout=1) and fed >= kill_after:
            break
        time.sleep(1.3)
    time.sleep(0.6)
    streaming.kill(p1)                                         # a crash of the WHOLE process tree: no shutdown hooks, no final commit
    killed_at = streaming.batches_written(out, "alert")
    for i in range(fed, len(files)):                           # input that arrives during the outage
        put(f"in-{i:05d}.json", files[i])
    put("in-zz-heartbeat.json", [heartbeat(events)])
    p2 = streaming.start(spec_p, src, out, ckpt, None, log=d / "run2.log", idle_exit_s=25, **common)
    p2.wait(timeout=300)
    streaming.kill(p2)
    alerts = streaming.read_kind(out, "alert")
    ids = [a["triggeringEventId"] for a in alerts]
    late = {x["triggeringEventId"] for x in streaming.read_kind(out, "late")}
    problems = []
    if len(ids) != len(set(ids)):
        problems.append(f"duplicate alerts after recovery: {sorted({i for i in ids if ids.count(i) > 1})[:5]}")
    ref_alerts = [a for s in scs for a in run_reference(compiled, [e for e in s.events if e["event_id"] not in late], s.policy).alerts]
    problems += compare(ref_alerts, alerts, True)
    nums = sorted(int(f.stem.split("-")[1]) for f in (out / "alert").glob("batch-*.jsonl"))
    if nums != list(range(nums[0], nums[-1] + 1)):
        problems.append(f"batch files are not contiguous: {nums}")

    # collector retry: deliver every file a second time under new names; only duplicate/late records may appear
    before = len(alerts)
    for i, rows in enumerate(files):
        put(f"retry-{i:05d}.json", rows)
    p3 = streaming.start(spec_p, src, out, ckpt, None, log=d / "run3.log", idle_exit_s=20, **common)
    p3.wait(timeout=300)
    streaming.kill(p3)
    after = streaming.read_kind(out, "alert")
    if len(after) != before:
        problems.append(f"re-delivery created {len(after) - before} new alert(s)")
    dups = len(streaming.read_kind(out, "duplicate")) + len(streaming.read_kind(out, "late"))
    dup_ids = {x["triggeringEventId"] for x in streaming.read_kind(out, "duplicate")}
    first_file_ids = {e["event_id"] for e in files[0]}
    if not (dup_ids & first_file_ids):
        problems.append("mid-stream redelivery of file 0 produced no `duplicate` record for its events")
    dropped = 0
    for f in (out / "progress").glob("batch-*.json"):
        dropped += sum(o.get("numRowsDroppedByWatermark", 0) for o in json.loads(f.read_text(encoding="utf-8")).get("stateOperators", []))
    return {"killAfterBatches": kill_after, "batchesBeforeKill": killed_at, "filesFedBeforeKill": fed, "totalFiles": len(files),
            "alerts": len(alerts), "expectedAlerts": len(ref_alerts), "duplicateOrLateRecords": dups, "duplicateRecordsForMidStreamRetry": len(dup_ids & first_file_ids),
            "rowsDroppedByWatermarkAfterHeartbeat": dropped,
            "batchFiles": len(nums), "problems": problems}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--behaviour", default="password-spray-across-accounts")
    ap.add_argument("--cases", type=int, default=30)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--kill-after", default="2,4,6")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="sf-recover-"))
    res = []
    for k in map(int, a.kill_after.split(",")):
        r = one_kill_point(a.behaviour, a.cases, a.seed, k, work)
        res.append(r)
        print(f"{'PASS' if not r['problems'] else 'FAIL'} kill after {k} batches: alerts={r['alerts']}/{r['expectedAlerts']} "
              f"batch-files={r['batchFiles']} mid-retry-dup-records={r['duplicateRecordsForMidStreamRetry']} late-retry-rows-dropped-by-watermark={r['rowsDroppedByWatermarkAfterHeartbeat']}", *r["problems"][:2])
    if a.out:
        a.out.write_text(json.dumps({"behaviour": a.behaviour, "seed": a.seed, "lateness": LATENESS, "expiry": EXPIRY, "runs": res}, indent=2), encoding="utf-8")
    ok = all(not r["problems"] for r in res)
    if ok:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"work directory kept for inspection: {work}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
