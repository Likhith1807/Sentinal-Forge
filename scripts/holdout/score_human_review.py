"""Score a filled-in human review sheet against the frozen gold.

    python scripts/holdout/score_human_review.py path/to/human_review_sheet.csv

Writes data/holdout/review/RESULT.json: label agreement, Cohen's kappa, and every disagreement (nothing is
adjudicated or edited here - disagreements are to be published next to the evaluation, not silently absorbed).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HOLD = Path(__file__).resolve().parents[2] / "data" / "holdout"


def kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in set(a) | set(b))
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main(path: str) -> int:
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r["your_label(compiled|no_compile)"].strip()]
    gold_l, rev_l, dis = [], [], []
    for r in rows:
        g = json.loads((HOLD / "gold" / f"{r['reportId']}.gold.json").read_text(encoding="utf-8"))
        gl = "compiled" if g["expect"] == "compiled" else "no_compile"
        rl = r["your_label(compiled|no_compile)"].strip()
        gold_l.append(gl), rev_l.append(rl)
        bad = gl != rl
        if not bad and gl == "compiled":
            bad = r["behaviourId"].strip() != g["behaviourId"] or (
                "count" in g and (str(g["count"]["value"]) != r["count"].strip() or str(g["window"]["seconds"]) != r["window_seconds"].strip()))
        if bad:
            dis.append({"reportId": r["reportId"], "gold": gl, "reviewer": rl, "comment": r["comment"]})
    res = {"reviewer": "human (independent)", "reports": len(rows), "labelAgreement": round(sum(a == b for a, b in zip(gold_l, rev_l)) / max(1, len(rows)), 4),
           "cohenKappaLabel": round(kappa(gold_l, rev_l), 4) if rows else None, "disagreements": dis}
    (HOLD / "review" / "RESULT.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "disagreements"}, indent=2), f"\n{len(dis)} disagreement(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
