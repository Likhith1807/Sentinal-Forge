"""The three-minute demonstration, without a browser: one detection, one justified rejection, one schema-change failure.

    python scripts/demo.py                    # reference engine: seconds, no JVM
    python scripts/demo.py --engine spark     # same steps, executed on Spark (needs JDK 8-17 and `sbt`-built classes)

It drives the same service layer the dashboard uses (`sentinelforge.service.workflow.Workspace`), in a throw-away state
directory, against the committed demonstration dataset (data/demo: 725 synthetic events with 34 planted, graded incidents).
Nothing here is precomputed: every line printed is produced by this run.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from sentinelforge.service.settings import Settings  # noqa: E402
from sentinelforge.service.workflow import Blocked, Workspace  # noqa: E402

DEMO = REPO / "data" / "demo" / "reports"
DONE_ANALYSIS = ("ready", "needs_review", "rejected", "failed")


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def wait(fn, done, timeout: float = 300.0):
    end = time.time() + timeout
    while time.time() < end:
        x = fn()
        if done(x):
            return x
        time.sleep(0.2)
    raise SystemExit("timed out waiting for a background job")


def analyse(ws: Workspace, name: str) -> dict:
    report = ws.create_report((DEMO / name).read_text(encoding="utf-8"), source="demo")
    sub = ws.submit_analysis(report["id"], user="demo")
    return wait(lambda: ws.get_analysis(sub["analysisId"]), lambda a: a["status"] in DONE_ANALYSIS)


def run_rule(ws: Workspace, rv_id: str) -> dict:
    sub = ws.submit_run(rv_id, user="demo")
    return wait(lambda: ws.get_run(sub["runId"]), lambda r: r["state"] in ("completed", "failed"))


def quote(e: dict) -> str:
    return f"\"{e['quote']}\" @ chars {e['start']}-{e['end']}" if e else "(none)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="reference", choices=("reference", "spark", "auto"))
    ap.add_argument("--keep", action="store_true", help="keep the throw-away state directory")
    args = ap.parse_args(argv)
    state = Path(tempfile.mkdtemp(prefix="sentinel-demo-"))
    ws = Workspace(Settings(state_dir=state, engine=args.engine))
    try:
        # ------------------------------------------------------------------ 1. a detection
        rule("1. A report becomes a detection - and every number is traceable to a quote")
        a = analyse(ws, "01-brute-force-vpn.md")
        rec = a["result"]["reconciliation"]
        cond = rec["spec"]["conditions"]
        print(f"analysis: {a['status']}   extractor: {a['extractor']} (no model: conditions come from the passage alone)")
        print(f"behaviour: {a['result']['behaviourId']}")
        print(f"  count  >= {cond['count']['value']}  ({cond['count']['semantics']})   evidence: {quote(cond['count']['evidence'])}")
        print(f"  window  {cond['window']['amount']} {cond['window']['unit']}  ({int(cond['window']['seconds'])} s)   evidence: {quote(cond['window']['evidence'])}")
        for u in rec["uncertainties"]:
            print(f"  note: {u}")
        rv = ws.create_rule_version(a["id"], "demo")
        print(f"rule version {rv['version']} ({rv['state']}), hash {rv['rule_hash']}, compiler {rv['compiler_version']}")
        run = run_rule(ws, rv["id"])
        alerts = ws.alerts(run["id"])
        print(f"run on {run['dataset']['name']!r} v{run['dataset_version']} with the {run['engine']} engine: {alerts['total']} alerts")
        for al in alerts["alerts"][:3]:
            print(f"  alert: account {al['groupKey']}  {al['matchedCount']} failures in [{al['windowStart']} .. {al['detectedAt']}]  trigger {al['triggeringEventId']}")
        ev = ws.evidence_record(run["id"], alerts["alerts"][0]["triggeringEventId"])
        print(f"evidence record (`{ev.get('recordVersion', '1')}`) answers: {', '.join(k for k in ('why', 'means', 'supported', 'fired', 'executed', 'uncertain') if k in ev)}")
        print("  it is an evidence record, not a proof: quotes are checked to exist at their offsets, not to mean what the rule encodes.")
        ws.decide(rv["id"], "approve", "demo", "matches the report", run["id"])
        print(f"approved by the analyst against rule version {rv['version']} (hash {rv['rule_hash']}).")

        # ------------------------------------------------------------------ 2. a justified rejection
        rule("2. A report the system refuses - with the reason, and the sentence that caused it")
        r = analyse(ws, "08-impossible-travel.md")
        rec = r["result"]["reconciliation"]
        print(f"analysis: {r['status']}   (nothing was compiled)")
        for reason in rec["reasons"][:3]:
            print(f"  [{reason['code']}] {reason['message']}")
            for e in reason.get("evidence", [])[:1]:
                print(f"      because of: {quote(e)}")
        c = analyse(ws, "09-contradictory-windows.md")
        print(f"\nand a report that contradicts itself: analysis {c['status']}")
        for reason in c["result"]["reconciliation"]["reasons"][:2]:
            print(f"  [{reason['code']}] {reason['message']}")
        s = analyse(ws, "06-ninety-seconds.md")
        w = s["result"]["reconciliation"]["spec"]["conditions"]["window"] if s["status"] == "ready" else None
        if w:
            print(f"\nand the audit's worst bug, closed: '90 seconds' compiles to {int(w['seconds'])} s, from {quote(w['evidence'])}")

        # ------------------------------------------------------------------ 3. a schema-change failure
        rule("3. The data changes under an approved rule - the impact is computed, not discovered in production")
        spray = analyse(ws, "02-password-spray.md")
        rv2 = ws.create_rule_version(spray["id"], "demo")
        run2 = run_rule(ws, rv2["id"])
        ws.decide(rv2["id"], "approve", "demo", "spray rule", run2["id"])
        print(f"two approved rules: brute-force (needs account_id) and password-spray (needs source_host)")
        print("schema change: the collector stops sending `source_host` ...")
        impact = ws.apply_schema_change("demo-auth", ["source_host"], None, "demo")
        for it in impact["affected"]:
            blocked = "; ".join(f"{b['condition']} (needs {', '.join(b['fields'])})" for b in it["blockedConditions"])
            print(f"  PAUSED     {it['ruleName'].split(' - ')[0]}: blocked - {blocked}")
        for it in impact["unaffected"]:
            print(f"  unaffected {it['ruleName']} (still {it['stateAfter']})")
        try:
            ws.resume_rule_version(rv2["id"], "demo")
            print("  resume was accepted (unexpected)")
        except Blocked as exc:
            print(f"  resume refused: {exc}")
        print("restoring the previous dataset version and revalidating the paused rule ...")
        ws.restore_dataset("demo-auth", 1, "demo")
        rerun = ws.resume_rule_version(rv2["id"], "demo")
        done = wait(lambda: ws.get_run(rerun["runId"]), lambda r: r["state"] in ("completed", "failed"))
        print(f"  revalidation run {done['state']}; rule state is now '{ws.get_rule_version(rv2['id'])['state']}' (a 'resume' decision, \"revalidated on current data\", is on record).")
        print("\nDone. The same steps, with the evidence panels and the dataset picker, are in the dashboard:  python -m uvicorn dashboard.backend.main:app")
        return 0
    finally:
        ws.shutdown()
        if not args.keep:
            shutil.rmtree(state, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
