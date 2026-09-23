"""End-to-end audit: does an extraction pipeline produce the *compiled rule the gold labels imply*?

This is the measurement behind the finding "the fine-tuned pipeline produced the correct compiled
specification for 27 of 40 supported reports, while 7 of 44 reports silently produced an incorrect or
unsupported rule". It runs the SAME reports through several pipelines so every fix has a number to move:

  legacy-finetuned   fine-tuned model -> Stage 3 -> bridge   (unit taken from the model's unit head;
                     reproduced from experiments/results/audit_pipeline_baseline.json, run before any fix)
  finetuned-raw      fine-tuned model (unit read from text) -> strict Stage 3 -> strict bridge; no evidence check
  finetuned+evidence fine-tuned model -> reconcile against the passage -> data check -> compile   [product]
  evidence-only      no model at all: the deterministic condition finder decides
  classical          the regex baseline, through the product path

Outcome per gold-supported report:
  correct              compiled rule == compiled(gold)
  wrong-rule-silent    a rule was compiled and it differs from the gold rule            <- the dangerous one
  needs-review         nothing compiled because evidence was absent / readings disagreed
  rejected-supported   nothing compiled although the report is supported and evidence conflicts
Per unsupported report:
  correctly-refused    nothing compiled
  silently-accepted    a rule was compiled for a behaviour the compiler cannot express  <- dangerous

The test split is used here because these reports are the *regression* set: the pipeline was tuned against
their failures, so this is development evidence. The frozen holdout (data/holdout) is the untouched one.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "nlp" / "src"), str(REPO / "compiler" / "src")]

import observability_checker as stage3  # noqa: E402
import spec_bridge  # noqa: E402
from sentinelforge.pipeline import analyze  # noqa: E402

CORPUS = REPO / "data" / "corpus"


def load_split(split: str):
    splits = json.loads((CORPUS / "splits.json").read_text(encoding="utf-8"))["reports"]
    for p in sorted((CORPUS / "gold").glob("*.gold.json")):
        g = json.loads(p.read_text(encoding="utf-8"))
        if splits[g["reportId"]] == split:
            yield g, (CORPUS / "reports" / f"{g['reportId']}.md").read_text(encoding="utf-8")


def gold_compiled(g: dict):
    return spec_bridge.build_compiled_spec(g) if g["supported"] else None


def functional(c):
    """Only the keys that decide what the rule does (metadata such as hashes/versions excluded)."""
    return None if c is None else {k: c.get(k) for k in spec_bridge.ALL_COMPILED_SPEC_KEYS}


def outcome(gold_rule, got, status):
    if gold_rule is None:
        return "silently-accepted" if got is not None else "correctly-refused"
    if got is None:
        return "needs-review" if status == "needs_review" else "rejected-supported"
    return "correct" if functional(got) == functional(gold_rule) else "wrong-rule-silent"


def as_extraction(r) -> dict:
    return {"behaviourId": r.behaviourId, "requiredFields": r.requiredFields, "policyFields": r.policyFields,
            "threshold": r.threshold, "timeWindow": r.timeWindow, "provenance": r.provenance}


def raw_pipeline(ext: dict):
    v = stage3.validate(ext)
    if v.status != "supported":
        return None, "rejected"
    try:
        return spec_bridge.build_compiled_spec(ext), "compiled"
    except spec_bridge.UnbuildableSpecError:
        return None, "rejected"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--out", default=str(REPO / "experiments" / "results" / "audit_pipeline_fixed.json"))
    args = ap.parse_args(argv)

    import classical_extractor
    import finetuned_extractor

    rows = []
    for g, text in load_split(args.split):
        want = gold_compiled(g)
        ft = finetuned_extractor.extract(text)
        ext = as_extraction(ft)
        cl = classical_extractor.extract(text)
        cl_ext = {"behaviourId": cl.behaviourId, "requiredFields": cl.requiredFields, "policyFields": cl.policyFields,
                  "threshold": cl.threshold, "timeWindow": cl.timeWindow, "provenance": cl.provenance}
        res = {}
        c, st = raw_pipeline(ext)
        res["finetuned-raw"] = (c, st, [])
        for name, extraction in (("finetuned+evidence", ext), ("evidence-only", None), ("classical", cl_ext)):
            a = analyze(text, extraction)
            res[name] = (a.compiled, a.status, a.reconciliation.codes)
        rows.append({"reportId": g["reportId"], "behaviour": g["behaviourId"], "supported": g["supported"],
                     "outcomes": {k: outcome(want, c, st) for k, (c, st, _) in res.items()},
                     "codes": {k: codes for k, (_, _, codes) in res.items() if codes}})

    systems = ["finetuned-raw", "finetuned+evidence", "evidence-only", "classical"]
    summary = {s: dict(Counter(r["outcomes"][s] for r in rows)) for s in systems}
    n_sup = sum(r["supported"] for r in rows)
    print(f"{len(rows)} reports ({n_sup} supported, {len(rows) - n_sup} unsupported), split={args.split}")
    for s in systems:
        c = summary[s]
        silent = c.get("wrong-rule-silent", 0) + c.get("silently-accepted", 0)
        print(f"  {s:20s} correct={c.get('correct', 0):3d}/{n_sup}  silent-failures={silent}  needs-review={c.get('needs-review', 0)}  "
              f"rejected-supported={c.get('rejected-supported', 0)}  correctly-refused={c.get('correctly-refused', 0)}")
    Path(args.out).write_text(json.dumps({"split": args.split, "n": len(rows), "supported": n_sup, "summary": summary,
                                          "rows": rows}, indent=2), encoding="utf-8")
    return rows


if __name__ == "__main__":
    main()
