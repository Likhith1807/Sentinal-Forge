"""Runs Stage 3's observability checker against three things:

1. A deliberately unsupported spec (requires source_ip) — must be rejected.
2. The Phase 0 canonical IR and the 5 held-out gold specs — must all be
   supported, since they're hand-authored to only need observable fields.
3. The REAL, LIVE output of both Phase 2 extractors on the 5 held-out
   reports — this is the actual integration test: what does Stage 3
   conclude when fed an imperfect, real extractor's output, not a
   hand-crafted one?

Usage:
    python compiler/test/evaluate_stage3.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "compiler" / "src"))
sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))

import observability_checker as stage3  # noqa: E402
import classical_extractor  # noqa: E402
import transformer_extractor  # noqa: E402

GOLD_DIR = REPO_ROOT / "data" / "samples" / "ir" / "gold"


def check(label: str, spec: dict, expected_status: str, results: list) -> None:
    result = stage3.validate(spec)
    ok = result.status == expected_status
    results.append({
        "case": label,
        "expected": expected_status,
        "actual": result.status,
        "match": ok,
        "notes": result.notes,
    })
    marker = "OK " if ok else "!! "
    print(f"{marker}{label:45s} expected={expected_status:10s} actual={result.status:10s}")
    for note in result.notes:
        print(f"      - {note}")


def main() -> None:
    results: list[dict] = []

    print("=== 1. Deliberately unsupported spec ===")
    check(
        "requires source_ip (known-unreliable field)",
        {"requiredFields": ["account_id", "event_type", "source_ip"], "policyFields": []},
        "rejected",
        results,
    )

    print("\n=== 2. Hand-authored gold specs (should all be supported) ===")
    b1_ir = json.loads((REPO_ROOT / "data/samples/ir/login-brute-force-001.json").read_text())
    check("Phase 0 canonical IR (B1, SF-SAMPLE-001)",
          {"requiredFields": [f["field"] for f in b1_ir["requiredFields"]], "policyFields": []},
          "supported", results)

    for gold_path in sorted(GOLD_DIR.glob("*.gold.json")):
        gold = json.loads(gold_path.read_text())
        check(f"gold: {gold['reportId']} ({gold['behaviourId']})",
              {"requiredFields": gold["requiredFields"], "policyFields": gold["policyFields"], "behaviourId": gold["behaviourId"]},
              "supported", results)

    print("\n=== 3. Live Phase 2 extractor output, fed straight into Stage 3 ===")
    for gold_path in sorted(GOLD_DIR.glob("*.gold.json")):
        gold = json.loads(gold_path.read_text())
        report_text = (REPO_ROOT / gold["reportFile"]).read_text(encoding="utf-8")

        classical_result = classical_extractor.extract(report_text)
        classical_spec = {
            "requiredFields": classical_result.requiredFields,
            "policyFields": classical_result.policyFields,
            "behaviourId": classical_result.behaviourId,
        }
        check(f"classical -> {gold['reportId']}", classical_spec, "supported", results)

        transformer_result = transformer_extractor.extract(report_text)
        transformer_spec = {
            "requiredFields": transformer_result["requiredFields"],
            "policyFields": transformer_result["policyFields"],
            "behaviourId": transformer_result["behaviourId"],
        }
        check(f"transformer -> {gold['reportId']}", transformer_spec, "supported", results)

    out_path = REPO_ROOT / "experiments" / "results" / "phase3_validation_eval.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
