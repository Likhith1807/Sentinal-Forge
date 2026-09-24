"""Frozen-holdout evaluation: does an extractor's output produce a CORRECT DETECTION, not just correct-looking JSON?

    python experiments/holdout/run_eval.py [--skip-llm]

Systems (same reports, same scoring, same downstream check):
  regex-raw / regex+evidence            classical extractor, without / with the evidence check
  prompted-raw                          the prompted LLM baseline AS BUILT (forced choice, no abstention option, its
                                        prompt example fixes the unit to "minutes")
  prompted-abstain-raw / +evidence      the same LLM with an abstention option added, so it is not judged on a handicap
  finetuned-raw / finetuned+evidence    the fine-tuned RoBERTa extractor, without / with the evidence check (the product)
  evidence-only                         no model at all: the deterministic condition finder

  "raw"      = proposal -> strict validation -> compile      (what the pipeline did before the audit's fixes)
  "+evidence"= proposal -> reconcile against the passage -> data check -> compile   (sentinelforge.pipeline.analyze)

Everything is scored separately (behaviour, count semantics, fields, count value, window seconds / unit, evidence)
and together (complete-specification correctness), then DOWNSTREAM: the compiled rule and the gold rule are run on the
demonstration dataset by the reference engine and their alerts compared. Reports labelled must-not-compile give false
accepts. FIRST-ATTEMPT results and every retry of the LLM calls are kept (results/holdout/attempts.jsonl).

The holdout is FROZEN (scripts/holdout/verify_frozen.py). This script never edits it and the extractors / parser are
not tuned after seeing these numbers; failures are published, not fixed here.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "nlp" / "src"), str(REPO / "compiler" / "src")]

import observability_checker as stage3  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402
from sentinelforge.compile import RuleBuildError, compile_spec  # noqa: E402
from sentinelforge.pipeline import analyze  # noqa: E402
from sentinelforge.refengine import run_reference  # noqa: E402

HOLD = REPO / "data" / "holdout"
OUT = REPO / "experiments" / "results" / "holdout"
DEMO_EVENTS = REPO / "data" / "demo" / "auth_events.jsonl"
DEMO_POLICY = REPO / "data" / "demo" / "account_policy.json"
FUNCTIONAL = ["behaviourId", "recipe", "groupingKey", "timeWindowSeconds", "countEventType", "countThreshold", "triggerEventType",
              "distinctField", "distinctThreshold", "filterEventType", "logField", "policyField", "comparisonOp"]

SYSTEMS = [("regex-raw", "classical", "raw"), ("regex+evidence", "classical", "evidence"),
           ("prompted-raw", "prompted", "raw"), ("prompted-abstain-raw", "prompted-abstain", "raw"),
           ("prompted-abstain+evidence", "prompted-abstain", "evidence"),
           ("finetuned-raw", "finetuned", "raw"), ("finetuned+evidence", "finetuned", "evidence"),
           ("evidence-only", None, "evidence")]

ABSTAIN_NOTE = ('\n\nIMPORTANT: if the report does not describe exactly one of these behaviours with every number stated explicitly, '
                'or needs anything the behaviours cannot express, set "behaviourId" to "none" (and leave threshold and timeWindow null). '
                'For "timeWindow" use the unit the report itself uses ("seconds", "minutes" or "hours").')


def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()


# ----------------------------------------------------------------------------------------- extractors
class LLM:
    """Prompted-LLM extractor with a durable response cache and quota awareness.

    Every API attempt (first tries and retries, successes and errors) is appended to attempts.jsonl. A successful raw
    response is remembered per (report, variant, model) in llm_cache.jsonl, so a later run - e.g. after a daily token
    quota resets - resumes instead of repeating calls. Once a "tokens per day" error appears no further calls are made
    in this run; the remaining reports are reported as UNAVAILABLE, never scored as wrong answers."""

    def __init__(self, log_path: Path, model: str):
        import transformer_extractor as te
        self.te, self.model = te, model
        self.log = open(log_path, "a", encoding="utf-8")
        self.cache_path = log_path.with_name("llm_cache.jsonl")
        self.cache: dict = {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.cache[(r["reportId"], r["variant"], r["model"])] = r
        self.exhausted = False

    def _parse(self, raw: str) -> dict:
        import re
        cleaned = self.te._strip_fence(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", cleaned, re.S)
            if not m:
                raise ValueError("unparseable JSON")
            return json.loads(m.group(0))

    def _to_proposal(self, result: dict) -> dict:
        te = self.te
        b = result.get("behaviourId")
        return {"behaviourId": None if b in (None, "none", "None", "null") else (b if b in B.BEHAVIOUR_IDS else "unrecognized:" + str(b)),
                "requiredFields": [f for f in (result.get("requiredFields") or []) if f in te.LOG_FIELDS],
                "policyFields": [f for f in (result.get("policyFields") or []) if f in te.POLICY_FIELDS],
                "threshold": result.get("threshold"), "timeWindow": result.get("timeWindow"), "provenance": {}}

    def extract(self, rid: str, text: str, variant: str, max_attempts: int = 3) -> dict:
        key = (rid, variant, self.model)
        if key in self.cache:
            c = self.cache[key]
            return {"proposal": self._to_proposal(self._parse(c["raw"])), "firstAttemptOk": c["firstAttemptOk"], "fromCache": True}
        if self.exhausted:
            return {"unavailable": True, "firstAttemptOk": None, "reason": "daily token quota exhausted earlier in this run"}
        te = self.te
        prompt = te.build_prompt(text) + (ABSTAIN_NOTE if variant == "prompted-abstain" else "")
        first_ok = None
        for attempt in range(1, max_attempts + 1):
            rec = {"reportId": rid, "variant": variant, "model": self.model, "attempt": attempt, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            try:
                raw = te.call_model(prompt, self.model)
                rec["raw"] = raw
                result = self._parse(raw)
                rec["ok"] = True
                first_ok = True if first_ok is None else first_ok
                self.log.write(json.dumps(rec) + "\n"), self.log.flush()
                entry = {"reportId": rid, "variant": variant, "model": self.model, "raw": raw, "firstAttemptOk": bool(first_ok)}
                self.cache[key] = entry
                with open(self.cache_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry) + "\n")
                return {"proposal": self._to_proposal(result), "firstAttemptOk": bool(first_ok)}
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - every failure is logged, never scored as an answer
                msg = f"{type(exc).__name__}: {str(exc)[:400]}"
                rec["ok"], rec["error"] = False, msg
                first_ok = False if first_ok is None else first_ok
                self.log.write(json.dumps(rec) + "\n"), self.log.flush()
                if "per day" in msg or "TPD" in msg:
                    self.exhausted = True
                    print(f"[llm] daily token quota exhausted at {rid}; remaining LLM rows will be UNAVAILABLE", flush=True)
                    break
                time.sleep(3.0 * attempt)
        return {"unavailable": True, "firstAttemptOk": False}


def make_extractors(skip_llm: bool, llm_model: str):
    import classical_extractor
    ex = {}

    def classical(rid, text):
        r = classical_extractor.extract(text)
        return {"proposal": {"behaviourId": r.behaviourId if r.behaviourId in B.BEHAVIOUR_IDS else None, "requiredFields": r.requiredFields,
                             "policyFields": r.policyFields, "threshold": r.threshold, "timeWindow": r.timeWindow, "provenance": r.provenance}}
    ex["classical"] = classical
    try:
        import finetuned_extractor

        def finetuned(rid, text):
            r = finetuned_extractor.extract(text)
            return {"proposal": {"behaviourId": r.behaviourId, "requiredFields": r.requiredFields, "policyFields": r.policyFields,
                                 "threshold": r.threshold, "timeWindow": r.timeWindow, "provenance": r.provenance}}
        ex["finetuned"] = finetuned
    except Exception as exc:  # noqa: BLE001
        print("fine-tuned extractor unavailable:", exc)
    if not skip_llm:
        llm = LLM(OUT / "attempts.jsonl", llm_model)
        ex["prompted"] = lambda rid, text: llm.extract(rid, text, "prompted")
        ex["prompted-abstain"] = lambda rid, text: llm.extract(rid, text, "prompted-abstain")
        ex["_llm"] = llm
    return ex


# -------------------------------------------------------------------------------------------- pipelines
def run_path(path: str, text: str, proposal: dict | None):
    """-> (compiled | None, status, behaviourId claimed/decided, spec-ish dict for scoring)"""
    if path == "raw":
        if proposal is None or proposal.get("behaviourId") is None or str(proposal["behaviourId"]).startswith("unrecognized"):
            return None, "refused", proposal.get("behaviourId") if proposal else None, None
        v = stage3.validate(proposal)
        if v.status != "supported":
            return None, "rejected", proposal["behaviourId"], None
        try:
            return compile_spec(proposal), "compiled", proposal["behaviourId"], proposal
        except RuleBuildError:
            return None, "rejected", proposal["behaviourId"], None
    a = analyze(text, proposal)
    return a.compiled, a.status, a.behaviourId or (proposal or {}).get("behaviourId"), a.reconciliation.spec


def gold_rule(g: dict):
    if g["expect"] != "compiled":
        return None
    beh = B.BEHAVIOURS[g["behaviourId"]]
    spec = {"behaviourId": beh.id}
    if beh.windowed:
        spec["threshold"] = {next(iter(beh.threshold_aliases)): g["count"]["value"]}
        spec["timeWindow"] = {"amount": g["window"]["seconds"], "unit": "seconds"}
    return compile_spec(spec)


def functional(c):
    return None if c is None else {k: c.get(k) for k in FUNCTIONAL}


_DEMO = None


def alerts_of(compiled: dict) -> set:
    global _DEMO
    if _DEMO is None:
        _DEMO = ([json.loads(l) for l in DEMO_EVENTS.read_text(encoding="utf-8").splitlines() if l.strip()],
                 json.loads(DEMO_POLICY.read_text(encoding="utf-8"))["records"])
    ev, pol = _DEMO
    return {a["triggeringEventId"] for a in run_reference(compiled, ev, pol).alerts if a["status"] == "alert"}


def overlap(pred: dict | None, gold: dict) -> bool:
    if not pred:
        return False
    lo, hi = max(pred["start"], gold["start"]), min(pred["end"], gold["end"])
    return hi > lo and (hi - lo) >= 0.5 * (gold["end"] - gold["start"])


def f1(pred: set, gold: set):
    tp = len(pred & gold)
    p = tp / len(pred) if pred else None
    r = tp / len(gold) if gold else None
    return p, r


# ----------------------------------------------------------------------------------------------- score
def score_one(g: dict, text: str, sysname: str, path: str, res: dict) -> dict:
    proposal = res.get("proposal")
    row = {"reportId": g["reportId"], "system": sysname, "expect": g["expect"], "reasonClass": g.get("reasonClass"),
           "unavailable": bool(res.get("unavailable")), "firstAttemptOk": res.get("firstAttemptOk", True)}
    if row["unavailable"]:
        return row
    compiled, status, beh, spec = run_path(path, text, proposal)
    want = gold_rule(g)
    row.update(status=status, compiled=compiled is not None, decidedBehaviour=beh,
               proposalBehaviour=(proposal or {}).get("behaviourId"))
    if g["expect"] == "no_compile":
        row["falseAccept"] = compiled is not None
        if compiled is not None:
            row["downstreamFP"] = len(alerts_of(compiled))
        return row
    beh_ok = beh == g["behaviourId"]
    row["behaviourCorrect"] = beh_ok
    gb = B.BEHAVIOURS[g["behaviourId"]]
    got_alerts = alerts_of(compiled) if compiled is not None else set()
    gold_alerts = alerts_of(want)
    tp = len(got_alerts & gold_alerts)
    row.update(completeCorrect=functional(compiled) == functional(want) and compiled is not None,
               wrongSilent=compiled is not None and functional(compiled) != functional(want),
               notCompiled=compiled is None, downstreamTP=tp, downstreamFP=len(got_alerts - gold_alerts), downstreamFN=len(gold_alerts - got_alerts))
    if gb.windowed:
        pc = compiled or {}
        pv = pc.get("countThreshold") or pc.get("distinctThreshold")
        # fall back to the raw proposal for component metrics when nothing compiled (component accuracy is measured independent of refusal)
        raw_thr = None
        if proposal and isinstance(proposal.get("threshold"), dict) and len(proposal["threshold"]) == 1:
            raw_thr = next(iter(proposal["threshold"].values()))
        raw_win = None
        tw = (proposal or {}).get("timeWindow")
        if isinstance(tw, dict) and isinstance(tw.get("amount"), (int, float)) and not isinstance(tw.get("amount"), bool):
            raw_win = tw["amount"] * {"seconds": 1, "minutes": 60, "hours": 3600}.get(tw.get("unit"), float("nan"))
        if path == "evidence" and spec:
            c = (spec.get("conditions") or {}).get("count")
            w = (spec.get("conditions") or {}).get("window")
            raw_thr, raw_win = (c or {}).get("value"), (w or {}).get("seconds")
            row["countSemanticsCorrect"] = bool(c) and c["semantics"] == gb.count_semantics
            row["evidenceCount"] = overlap((c or {}).get("evidence"), g["count"]["evidence"])
            row["evidenceWindow"] = overlap((w or {}).get("evidence"), g["window"]["evidence"])
            row["quoteVerified"] = all(text[e["start"]:e["end"]] == e["quote"] for e in [(c or {}).get("evidence"), (w or {}).get("evidence")] if e)
        else:
            key = next(iter(proposal["threshold"])) if proposal and isinstance(proposal.get("threshold"), dict) and len(proposal["threshold"]) == 1 else None
            row["countSemanticsCorrect"] = key is not None and gb.threshold_aliases.get(key) == gb.count_semantics
            prov = (proposal or {}).get("provenance") or {}
            t, w = prov.get("threshold"), prov.get("timeWindow")
            if t or w:
                row["evidenceCount"] = bool(t) and overlap({"start": t["charStart"], "end": t["charEnd"]}, g["count"]["evidence"])
                row["evidenceWindow"] = bool(w) and overlap({"start": w["charStart"], "end": w["charEnd"]}, g["window"]["evidence"])
        row["countCorrect"] = raw_thr == g["count"]["value"]
        row["windowCorrect"] = raw_win == g["window"]["seconds"]
        if isinstance(tw, dict) and raw_win is not None and raw_win != g["window"]["seconds"] and tw.get("amount") == g["window"]["seconds"] / {"seconds": 1, "minutes": 60, "hours": 3600}.get(tw.get("unit"), 1):
            row["unitError"] = True
    if proposal:
        pf = set(proposal.get("requiredFields", [])) | set(proposal.get("policyFields", []))
        gf = set(g["fields"])
        tp_f = len(pf & gf)
        row["fieldPrecision"] = tp_f / len(pf) if pf else None
        row["fieldRecall"] = tp_f / len(gf) if gf else None
    return row


def boot(rows, key, n=2000, seed=11):
    vals = [r[key] for r in rows if key in r and r[key] is not None]
    if not vals:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(n))
    return {"value": round(sum(vals) / len(vals), 4), "n": len(vals), "ci95": [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n)], 4)]}


def summarise(rows: list[dict]) -> dict:
    sup = [r for r in rows if r["expect"] == "compiled" and not r["unavailable"]]
    nos = [r for r in rows if r["expect"] == "no_compile" and not r["unavailable"]]
    for r in sup:
        r["completeCorrectF"] = float(r.get("completeCorrect", False))
        r["wrongSilentF"] = float(r.get("wrongSilent", False))
        r["notCompiledF"] = float(r.get("notCompiled", False))
        r["behaviourF"] = float(r.get("behaviourCorrect", False))
    for r in nos:
        r["falseAcceptF"] = float(r.get("falseAccept", False))
    for r in rows:
        for k_src, k_dst in (("countCorrect", "countF"), ("windowCorrect", "windowF"), ("countSemanticsCorrect", "semF"),
                             ("evidenceCount", "evCountF"), ("evidenceWindow", "evWinF"), ("quoteVerified", "qvF")):
            if k_src in r:
                r[k_dst] = float(r[k_src])
    tp = sum(r.get("downstreamTP", 0) for r in sup)
    fp = sum(r.get("downstreamFP", 0) for r in sup) + sum(r.get("downstreamFP", 0) for r in nos)
    fn = sum(r.get("downstreamFN", 0) for r in sup)
    p = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    by_reason = defaultdict(lambda: [0, 0])
    for r in nos:
        by_reason[r["reasonClass"]][0] += 1
        by_reason[r["reasonClass"]][1] += int(r.get("falseAccept", False))
    return {
        "supportedReports": len(sup), "mustNotCompileReports": len(nos), "unavailable": sum(1 for r in rows if r["unavailable"]),
        "coverage": round(1 - sum(1 for r in rows if r["unavailable"]) / max(1, len(rows)), 4), "reportsRequested": len(rows),
        "completeSpecCorrect": boot(sup, "completeCorrectF"), "wrongRuleSilently": boot(sup, "wrongSilentF"),
        "supportedNotCompiled": boot(sup, "notCompiledF"), "unsupportedAccepted": boot(nos, "falseAcceptF"),
        "behaviour": boot(sup, "behaviourF"), "countSemantics": boot(sup, "semF"), "countValue": boot(sup, "countF"),
        "windowSeconds": boot(sup, "windowF"), "unitErrors": sum(1 for r in sup if r.get("unitError")),
        "evidenceCountSpan": boot(sup, "evCountF"), "evidenceWindowSpan": boot(sup, "evWinF"), "quoteVerificationRate": boot(sup, "qvF"),
        "fieldPrecision": boot(sup, "fieldPrecision"), "fieldRecall": boot(sup, "fieldRecall"),
        "downstream": {"tp": tp, "fp": fp, "fn": fn, "precision": None if p is None else round(p, 4), "recall": None if rec is None else round(rec, 4),
                       "f1": None if not (p and rec) else round(2 * p * rec / (p + rec), 4)},
        "falseAcceptByReasonClass": {k: {"n": v[0], "accepted": v[1]} for k, v in sorted(by_reason.items())},
        "statusCounts": dict(Counter(r.get("status") for r in rows if not r["unavailable"])),
        "firstAttemptFailures": sum(1 for r in rows if r.get("firstAttemptOk") is False),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--holdout", default="holdout", help="directory under data/ (holdout | holdout_v2)")
    ap.add_argument("--llm-model", default="openai/gpt-oss-20b", help="model for the prompted rows (the 120B model's daily quota was exhausted during development)")
    args = ap.parse_args(argv)
    global HOLD, OUT
    HOLD = REPO / "data" / args.holdout
    if args.holdout != "holdout":
        OUT = REPO / "experiments" / "results" / args.holdout
    OUT.mkdir(parents=True, exist_ok=True)
    frozen = subprocess.run([sys.executable, str(REPO / "scripts" / "holdout" / "verify_frozen.py"), "--dir", str(HOLD)], capture_output=True, text=True)
    if frozen.returncode != 0:
        print(frozen.stdout)
        return 2
    golds = {p.name.split(".")[0]: json.loads(p.read_text(encoding="utf-8")) for p in sorted((HOLD / "gold").glob("*.gold.json"))}
    ids = sorted(golds)[: args.limit or None]
    ex = make_extractors(args.skip_llm, args.llm_model)
    raw_out: dict = {}
    per_report = open(OUT / "per_report.jsonl", "w", encoding="utf-8")
    all_rows: dict = defaultdict(list)
    t0 = time.time()
    for n, rid in enumerate(ids, 1):
        text = (HOLD / "reports" / f"{rid}.md").read_text(encoding="utf-8")
        g = golds[rid]
        cache: dict = {}
        for sysname, extractor, path in SYSTEMS:
            if extractor is not None and extractor not in ex:
                continue
            if extractor not in cache:
                cache[extractor] = ex[extractor](rid, text) if extractor else {"proposal": None}
                if extractor and extractor.startswith("prompted"):
                    time.sleep(1.2)
            row = score_one(g, text, sysname, path, cache[extractor])
            row["proposal"] = cache[extractor].get("proposal")
            all_rows[sysname].append(row)
            per_report.write(json.dumps(row, default=str) + "\n")
        if n % 20 == 0:
            print(f"{n}/{len(ids)} reports, {time.time() - t0:.0f}s", flush=True)
    summary = {name: summarise(rows) for name, rows in all_rows.items()}
    out = {"holdout": {"reports": len(ids), "frozen": json.loads((HOLD / "FROZEN.json").read_text())["frozenOn"], "tag": "holdout-v1-frozen" if args.holdout == "holdout" else f"{args.holdout.replace('holdout_', 'holdout-')}-frozen"},
           "gitHead": git_head(), "ranAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "llmModel": args.llm_model if not args.skip_llm else None,
           "note": "Single run on the frozen holdout. No extractor or parser change was made after seeing these numbers.", "systems": summary}
    (OUT / "summary.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    hdr = f"{'system':28s} {'complete':>9s} {'silent-wrong':>12s} {'not-compiled':>12s} {'false-accept':>12s} {'beh':>6s} {'count':>6s} {'window':>7s}  downstream P/R/F1"
    print(hdr)
    for name, s in summary.items():
        g = lambda k: (f"{s[k]['value']:.2f}" if s.get(k) else "  -  ")  # noqa: E731
        d = s["downstream"]
        print(f"{name:28s} {g('completeSpecCorrect'):>9s} {g('wrongRuleSilently'):>12s} {g('supportedNotCompiled'):>12s} {g('unsupportedAccepted'):>12s} "
              f"{g('behaviour'):>6s} {g('countValue'):>6s} {g('windowSeconds'):>7s}  {d['precision']}/{d['recall']}/{d['f1']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
