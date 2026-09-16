"""Runs both extractors against the 5 held-out reports and computes
extraction precision/recall/F1 against the hand-authored gold labels in
data/samples/ir/gold/. This is the number docs/behaviours.md and the
project README call "extraction F1" — computed for real here, not asserted.

Usage:
    python nlp/src/evaluate.py
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import classical_extractor
import transformer_extractor

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_DIR = REPO_ROOT / "data" / "samples" / "ir" / "gold"


def gold_field_set(gold: dict) -> set[str]:
    return set(gold.get("requiredFields", [])) | set(gold.get("policyFields", []))


def prf1(gold: set[str], pred: set[str]) -> tuple[float, float, float, int, int, int]:
    tp = len(gold & pred)
    fp = len(pred - gold)
    fn = len(gold - pred)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1, tp, fp, fn


def run_system(name: str, extract_fn, gold_files: list[Path]) -> dict:
    rows = []
    total_tp = total_fp = total_fn = 0
    behaviour_correct = 0

    for gold_path in gold_files:
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        report_path = REPO_ROOT / gold["reportFile"]
        report_text = report_path.read_text(encoding="utf-8")

        result = extract_fn(report_text)
        if hasattr(result, "requiredFields"):  # classical: dataclass
            pred_fields = set(result.requiredFields) | set(result.policyFields)
            pred_behaviour = result.behaviourId
            provenance = result.provenance
            provenance_verified = len(provenance)  # every classical span is a real regex match
        else:  # transformer: dict
            pred_fields = set(result.get("requiredFields", [])) | set(result.get("policyFields", []))
            pred_behaviour = result.get("behaviourId", "")
            provenance = result.get("provenance", {})
            provenance_verified = sum(1 for v in provenance.values() if v.get("verified"))

        gold_fields = gold_field_set(gold)
        precision, recall, f1, tp, fp, fn = prf1(gold_fields, pred_fields)
        total_tp += tp
        total_fp += fp
        total_fn += fn
        behaviour_match = gold["behaviourId"] == pred_behaviour
        behaviour_correct += int(behaviour_match)

        rows.append({
            "report": gold["reportId"],
            "goldBehaviour": gold["behaviourId"],
            "predBehaviour": pred_behaviour,
            "behaviourMatch": behaviour_match,
            "goldFields": sorted(gold_fields),
            "predFields": sorted(pred_fields),
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "provenanceEntries": len(provenance),
            "provenanceVerified": provenance_verified,
        })

    micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 1.0
    micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 1.0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) else 0.0

    total_provenance_entries = sum(r["provenanceEntries"] for r in rows)
    total_provenance_verified = sum(r["provenanceVerified"] for r in rows)

    return {
        "system": name,
        "perReport": rows,
        "microPrecision": round(micro_p, 3),
        "microRecall": round(micro_r, 3),
        "microF1": round(micro_f1, 3),
        "behaviourClassificationAccuracy": round(behaviour_correct / len(gold_files), 3),
        "provenanceVerificationRate": round(total_provenance_verified / total_provenance_entries, 3) if total_provenance_entries else None,
    }


def main() -> None:
    gold_files = sorted(GOLD_DIR.glob("*.gold.json"))
    if not gold_files:
        raise SystemExit(f"No gold files found under {GOLD_DIR}")

    results = {
        "classical": run_system("classical", classical_extractor.extract, gold_files),
        "transformer": run_system("transformer", transformer_extractor.extract, gold_files),
    }

    out_path = REPO_ROOT / "experiments" / "results" / "phase2_extraction_eval.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    for system_name, r in results.items():
        print(f"\n=== {system_name} ===")
        print(f"  micro P={r['microPrecision']}  R={r['microRecall']}  F1={r['microF1']}"
              f"  | behaviour classification acc={r['behaviourClassificationAccuracy']}"
              f"  | provenance verification rate={r['provenanceVerificationRate']}")
        for row in r["perReport"]:
            print(f"    {row['report']:14s} behaviour={'OK ' if row['behaviourMatch'] else 'ERR'}"
                  f"  P={row['precision']:.2f} R={row['recall']:.2f} F1={row['f1']:.2f}"
                  f"  pred={row['predFields']}")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
