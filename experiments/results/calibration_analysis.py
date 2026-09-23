"""Phase 5 calibration check — v2, re-run against the corpus's real n=44 test split.

**Superseded, not silently replaced.** The original version of this script (see git history,
`experiments/results/phase5_calibration_check.json` before this pass) measured cross-extractor
agreement (classical vs. the Phase 2 prompted-LLM extractor, then called "transformer") on n=5
held-out reports — the only held-out set that existed before the v2 corpus (`docs/corpus.md`).
`experiments/results/README.md`'s own "What's honestly still missing" section named that n=5 as
too small. The v2 corpus's 44-report test split (`docs/phase-c-extraction.md`) now exists and was
never touched during Phase C's model selection, so it is used here instead — closing that gap with
real data rather than more of the same small sample.

Two changes from v1, both stated plainly:

1. **n=5 -> n=44** (the corpus test split), computed fresh here (classical_extractor and
   finetuned_extractor both run locally — no LLM call, no quota risk, unlike the prompted
   extractor Phase C already found strains its own daily quota).
2. **The second system is now the fine-tuned model, not the prompted LLM.** Phase C's own
   ablation 1 found classical "far too weak on this corpus to usefully vote on anything" against
   the prompted/hybrid systems, and separately found the prompted extractor's abstention recall is
   0.0 at two model sizes — a worse candidate for a calibration signal, not just an unavailable
   one. The fine-tuned model is also the system the hybrid design (`docs/phase-c-extraction.md`)
   actually ships confidence-gated fallback around, so classical-vs-fine-tuned agreement is the
   more relevant real-world proxy now that a trained model exists to compare against.

A second, genuinely new signal is added rather than assumed unavailable: Phase 5's original
docstring said "No system in this project emits a self-reported confidence score" — true when
written, no longer true. `finetuned_extractor.extract` now returns a real softmax
`behaviourConfidence` (`nlp/src/finetuned_extractor.py`), the same score `hybrid_extractor.py`
already gates its fallback decision on. Both signals — cross-extractor agreement and the model's
own confidence — are measured against real correctness here, not assumed to correlate.

**The real result at n=44 does not repeat the n=5 finding, and that's reported honestly rather
than smoothed over.** Classical is near-universally in disagreement with the fine-tuned model
(Phase C's own ablation already found classical "far too weak on this corpus to usefully vote on
anything" — this is that finding, restated as a calibration signal): the disagreement rule ends up
flagging essentially every report, so its precision collapses to roughly the base rate of
imperfect extractions — no better than flagging everything. The model's own softmax confidence,
unavailable at n=5, is the signal that actually correlates with correctness here. The practical
conclusion this data supports is not "cross-extractor agreement works," it's "use the fine-tuned
model's own confidence, exactly as `hybrid_extractor.py` already does" — a real update to the n=5
hypothesis, not a confirmation of it.

Usage:
    python experiments/results/calibration_analysis.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))

import classical_extractor  # noqa: E402
import finetuned_extractor  # noqa: E402
from corpus_dataset import CORPUS_DIR  # noqa: E402

SPLIT = "test"


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def field_set(required, policy) -> set:
    return set(required) | set(policy)


def per_report_f1(gold_fields: set, pred_fields: set) -> float:
    tp = len(gold_fields & pred_fields)
    fp = len(pred_fields - gold_fields)
    fn = len(gold_fields - pred_fields)
    if tp == 0 and fp == 0 and fn == 0:
        return 1.0
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    return cov / (sx * sy) if sx > 0 and sy > 0 else None


def main() -> None:
    splits = json.loads((CORPUS_DIR / "splits.json").read_text(encoding="utf-8"))["reports"]
    golds = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((CORPUS_DIR / "gold").glob("*.gold.json"))]
    golds = [g for g in golds if splits[g["reportId"]] == SPLIT]
    print(f"{len(golds)} reports in split={SPLIT}")

    rows = []
    for gold in golds:
        text = (CORPUS_DIR / "reports" / f"{gold['reportId']}.md").read_text(encoding="utf-8")
        gold_fields = field_set(gold["requiredFields"], gold["policyFields"])

        c_pred = classical_extractor.extract(text)
        t_pred = finetuned_extractor.extract(text, model_dir=finetuned_extractor.DEFAULT_MODEL_DIR)

        c_fields = field_set(c_pred.requiredFields, c_pred.policyFields)
        t_fields = field_set(t_pred.requiredFields, t_pred.policyFields)

        agreement = jaccard(c_fields, t_fields)
        classical_f1 = per_report_f1(gold_fields, c_fields)
        finetuned_f1 = per_report_f1(gold_fields, t_fields)
        confidence = t_pred.raw.get("behaviourConfidence")

        rows.append({
            "report": gold["reportId"],
            "tier": gold["tier"],
            "supported": gold["supported"],
            "agreement": round(agreement, 3),
            "classicalF1": round(classical_f1, 3),
            "finetunedF1": round(finetuned_f1, 3),
            "finetunedConfidence": round(confidence, 3) if confidence is not None else None,
            "fullAgreement": agreement == 1.0,
        })

    n = len(rows)
    agreements = [r["agreement"] for r in rows]
    finetuned_f1s = [r["finetunedF1"] for r in rows]
    confidences = [r["finetunedConfidence"] for r in rows if r["finetunedConfidence"] is not None]
    confidence_f1s = [r["finetunedF1"] for r in rows if r["finetunedConfidence"] is not None]

    pearson_agreement = pearson(agreements, finetuned_f1s)
    pearson_confidence = pearson(confidences, confidence_f1s) if len(confidences) >= 2 else None

    # The decision rule this analysis actually proposes: flag for mandatory analyst review
    # whenever the two independently-implemented extractors disagree at all.
    flagged = [r for r in rows if not r["fullAgreement"]]
    not_flagged = [r for r in rows if r["fullAgreement"]]
    imperfect_flagged = sum(1 for r in flagged if r["finetunedF1"] < 1.0)
    total_imperfect = sum(1 for r in rows if r["finetunedF1"] < 1.0)
    rule_precision = imperfect_flagged / len(flagged) if flagged else None
    rule_recall = imperfect_flagged / total_imperfect if total_imperfect else None
    false_flags = sum(1 for r in not_flagged if r["finetunedF1"] < 1.0)  # imperfect but NOT flagged
    base_rate_imperfect = total_imperfect / n

    # A second, more useful decision rule this data actually supports: hybrid_extractor.py's own
    # confidence threshold (0.6) — does gating on the fine-tuned model's own softmax confidence
    # separate imperfect from perfect extractions better than the disagreement rule above?
    CONF_THRESHOLD = 0.6
    conf_flagged = [r for r in rows if r["finetunedConfidence"] is not None and r["finetunedConfidence"] < CONF_THRESHOLD]
    conf_not_flagged = [r for r in rows if r["finetunedConfidence"] is not None and r["finetunedConfidence"] >= CONF_THRESHOLD]
    conf_imperfect_flagged = sum(1 for r in conf_flagged if r["finetunedF1"] < 1.0)
    conf_precision = conf_imperfect_flagged / len(conf_flagged) if conf_flagged else None
    conf_recall = conf_imperfect_flagged / total_imperfect if total_imperfect else None
    conf_false_flags = sum(1 for r in conf_not_flagged if r["finetunedF1"] < 1.0)

    print("=== Calibration v2: cross-extractor agreement + self-reported confidence vs. fine-tuned F1 ===")
    for r in rows:
        tag = "FULL AGREEMENT" if r["fullAgreement"] else "DISAGREEMENT -> would flag for review"
        print(f"  {r['report']:10s} agreement={r['agreement']:.3f}  confidence={r['finetunedConfidence']}  "
              f"finetunedF1={r['finetunedF1']:.3f}  {tag}")

    print(f"\nn = {n} (corpus test split — up from n=5 in the superseded v1 run)")
    print(f"Base rate of imperfect (F1<1.0) extractions: {base_rate_imperfect:.3f}")
    print(f"Pearson r (agreement vs. fine-tuned F1) = {pearson_agreement}")
    print(f"Pearson r (self-reported confidence vs. fine-tuned F1) = {pearson_confidence}")
    print(f"Rule A 'disagreement -> flag': flagged {len(flagged)}/{n}, "
          f"precision={rule_precision}, recall={rule_recall}, missed={false_flags}")
    print(f"Rule B 'confidence<{CONF_THRESHOLD} -> flag' (hybrid_extractor.py's own threshold): "
          f"flagged {len(conf_flagged)}/{n}, precision={conf_precision}, recall={conf_recall}, missed={conf_false_flags}")

    result = {
        "n": n,
        "split": SPLIT,
        "supersedes": "the n=5 v1 run (classical vs. prompted-LLM), see this file's module docstring",
        "secondSystem": "fine-tuned (roberta-base)",
        "rows": rows,
        "pearsonAgreementVsF1": round(pearson_agreement, 3) if pearson_agreement is not None else None,
        "pearsonConfidenceVsF1": round(pearson_confidence, 3) if pearson_confidence is not None else None,
        "baseRateImperfect": round(base_rate_imperfect, 3),
        "disagreementRule": {
            "flagged": len(flagged),
            "notFlagged": len(not_flagged),
            "precision": round(rule_precision, 3) if rule_precision is not None else None,
            "recall": round(rule_recall, 3) if rule_recall is not None else None,
            "imperfectButNotFlagged": false_flags,
        },
        "confidenceThresholdRule": {
            "threshold": CONF_THRESHOLD,
            "note": "matches hybrid_extractor.DEFAULT_CONFIDENCE_THRESHOLD",
            "flagged": len(conf_flagged),
            "notFlagged": len(conf_not_flagged),
            "precision": round(conf_precision, 3) if conf_precision is not None else None,
            "recall": round(conf_recall, 3) if conf_recall is not None else None,
            "imperfectButNotFlagged": conf_false_flags,
        },
        "caveat": "n=44, the corpus's real test split, never touched during Phase C training or "
                  "model selection — the largest held-out set available in this project, not a "
                  "claim beyond what 44 reports supports.",
    }
    out_path = REPO_ROOT / "experiments/results/phase5_calibration_check.json"
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
