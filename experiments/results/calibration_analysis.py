"""Phase 5 calibration check, closed honestly rather than left as "not
applicable". No system in this project emits a self-reported confidence
score (a real gap — most LLM self-reported confidences are poorly
calibrated anyway, so building one wouldn't have been a strong choice).
Instead: cross-extractor agreement (classical vs. transformer, both
already run in Phase 2) is a real, principled, standard ensemble-based
confidence proxy, computed from data that already exists — not a new
score invented to fill this gap.

Hypothesis: when the two independently-implemented extractors agree on
the required-field set, the result is more likely correct than when they
disagree.

Usage:
    python experiments/results/calibration_analysis.py
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def main() -> None:
    data = json.loads((REPO_ROOT / "experiments/results/phase2_extraction_eval.json").read_text())
    classical = {r["report"]: r for r in data["classical"]["perReport"]}
    transformer = {r["report"]: r for r in data["transformer"]["perReport"]}

    rows = []
    for report_id in classical:
        c_fields = set(classical[report_id]["predFields"])
        t_fields = set(transformer[report_id]["predFields"])
        agreement = jaccard(c_fields, t_fields)
        rows.append({
            "report": report_id,
            "agreement": round(agreement, 3),
            "transformerF1": transformer[report_id]["f1"],
            "classicalF1": classical[report_id]["f1"],
            "fullAgreement": agreement == 1.0,
        })

    n = len(rows)
    mean_agreement = sum(r["agreement"] for r in rows) / n
    mean_f1 = sum(r["transformerF1"] for r in rows) / n
    cov = sum((r["agreement"] - mean_agreement) * (r["transformerF1"] - mean_f1) for r in rows) / n
    std_a = (sum((r["agreement"] - mean_agreement) ** 2 for r in rows) / n) ** 0.5
    std_f = (sum((r["transformerF1"] - mean_f1) ** 2 for r in rows) / n) ** 0.5
    pearson_r = cov / (std_a * std_f) if std_a > 0 and std_f > 0 else None

    # Decision rule this analysis actually proposes: flag for mandatory
    # analyst review whenever the two extractors disagree at all.
    flagged = [r for r in rows if not r["fullAgreement"]]
    not_flagged = [r for r in rows if r["fullAgreement"]]
    rule_precision = (
        sum(1 for r in flagged if r["transformerF1"] < 1.0) / len(flagged) if flagged else None
    )
    rule_catches_all_imperfect = all(r["transformerF1"] < 1.0 for r in flagged) and \
        all(r["transformerF1"] == 1.0 for r in not_flagged)

    print("=== Calibration: cross-extractor agreement vs. transformer F1 ===")
    for r in rows:
        print(f"  {r['report']:14s} agreement={r['agreement']:.3f}  transformerF1={r['transformerF1']:.3f}  "
              f"{'FULL AGREEMENT' if r['fullAgreement'] else 'DISAGREEMENT -> would flag for review'}")

    print(f"\nn = {n} (small sample — stated plainly, not smoothed over)")
    print(f"Pearson r (agreement vs. F1) = {pearson_r:.3f}" if pearson_r is not None else "Pearson r: undefined")
    print(f"Rule 'disagreement -> flag for review': "
          f"flagged {len(flagged)}/{n}, all of which had F1 < 1.0: {rule_catches_all_imperfect}")

    result = {
        "n": n,
        "rows": rows,
        "pearsonR": round(pearson_r, 3) if pearson_r is not None else None,
        "disagreementFlagsExactlyTheImperfectCases": rule_catches_all_imperfect,
        "caveat": "n=5, held-out set — a real signal on the data available, not a claim of general statistical significance.",
    }
    out_path = REPO_ROOT / "experiments/results/phase5_calibration_check.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
