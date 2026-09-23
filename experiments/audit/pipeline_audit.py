"""End-to-end audit of extraction -> Stage 3 -> bridge: does the fine-tuned pipeline produce the
*compiled specification the gold labels imply*?  This is the measurement behind the "27 of 40
supported reports correct; 7 of 44 silently wrong" finding, reproduced here so every fix has a
number to move.

Outcome taxonomy per report (gold-supported reports):
  correct              compiled spec == compiled(gold)
  wrong-spec-silent    a rule was compiled, but it differs from the gold rule   <- the dangerous one
  rejected-supported   pipeline refused a report that is supported            <- annoying, safe
Per unsupported report:
  correctly-rejected   no rule compiled
  silently-accepted    a rule was compiled for behaviour the compiler cannot express  <- dangerous
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "nlp" / "src"), str(REPO / "compiler" / "src")]

import observability_checker as stage3  # noqa: E402
import spec_bridge  # noqa: E402

CORPUS = REPO / "data" / "corpus"


def load_split(split: str):
    splits = json.loads((CORPUS / "splits.json").read_text(encoding="utf-8"))["reports"]
    for p in sorted((CORPUS / "gold").glob("*.gold.json")):
        g = json.loads(p.read_text(encoding="utf-8"))
        if splits[g["reportId"]] == split:
            yield g, (CORPUS / "reports" / f"{g['reportId']}.md").read_text(encoding="utf-8")


def pipeline(extraction: dict):
    """The path the dashboard/CLI take today: Stage 3 verdict, then the bridge."""
    v = stage3.validate(extraction)
    if v.status != "supported":
        return None, "stage3: " + "; ".join(v.notes)[:160]
    try:
        return spec_bridge.build_compiled_spec(extraction), None
    except spec_bridge.UnbuildableSpecError as e:
        return None, f"bridge: {e}"[:160]


def gold_compiled(g: dict):
    if not g["supported"]:
        return None
    return spec_bridge.build_compiled_spec(g)


def main():
    import finetuned_extractor
    rows = []
    for g, text in load_split("test"):
        r = finetuned_extractor.extract(text)
        ext = {"behaviourId": r.behaviourId, "requiredFields": r.requiredFields, "policyFields": r.policyFields,
               "threshold": r.threshold, "timeWindow": r.timeWindow}
        got, why = pipeline(ext)
        want = gold_compiled(g)
        if want is None:
            outcome = "correctly-rejected" if got is None else "silently-accepted"
        elif got is None:
            outcome = "rejected-supported"
        elif got == want:
            outcome = "correct"
        else:
            outcome = "wrong-spec-silent"
        diff = {k: (want.get(k) if want else None, got.get(k)) for k in (got or {}) if want is None or got.get(k) != want.get(k)} if got else {}
        rows.append({"reportId": g["reportId"], "behaviour": g["behaviourId"], "outcome": outcome, "diff": diff,
                     "rejectReason": why, "goldWindow": g.get("timeWindow"), "predWindow": r.timeWindow})
    from collections import Counter
    c = Counter(r["outcome"] for r in rows)
    print(dict(c))
    for r in rows:
        if r["outcome"] in ("wrong-spec-silent", "silently-accepted", "rejected-supported"):
            print(r["reportId"], r["outcome"], r["behaviour"], r["diff"], r["rejectReason"] or "")
    out = REPO / "experiments" / "results" / "audit_pipeline_baseline.json"
    out.write_text(json.dumps({"counts": c, "rows": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
