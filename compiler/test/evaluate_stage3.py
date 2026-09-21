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
import spec_bridge  # noqa: E402

GOLD_DIR = REPO_ROOT / "data" / "samples" / "ir" / "gold"


def expected_status(spec: dict) -> str:
    """Independently recomputes what Stage 3 SHOULD say for real extractor
    output, using the same structural-dependency facts Stage 3 itself
    uses (spec_bridge.structural_dependencies) — deliberately not calling
    stage3.validate() here, so this stays an independent check on the
    checker rather than the checker grading itself. A real extractor can
    legitimately omit a structurally-needed field (that's exactly the
    class of bug round 2 of the independent review found); when it does,
    'rejected' is the CORRECT expectation, not a failure of this test."""
    behaviour_id = spec.get("behaviourId")
    log_deps, policy_deps = spec_bridge.structural_dependencies(behaviour_id)
    requested = set(spec.get("requiredFields", [])) | set(spec.get("policyFields", []))
    return "supported" if (log_deps | policy_deps) <= requested else "rejected"


def check(label: str, spec: dict, expected: str, results: list) -> None:
    result = stage3.validate(spec)
    ok = result.status == expected
    results.append({
        "case": label,
        "expected": expected,
        "actual": result.status,
        "match": ok,
        "notes": result.notes,
    })
    marker = "OK " if ok else "!! "
    print(f"{marker}{label:45s} expected={expected:10s} actual={result.status:10s}")
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
          {"behaviourId": b1_ir["behaviourId"],
           "requiredFields": [f["field"] for f in b1_ir["requiredFields"]], "policyFields": []},
          "supported", results)

    for gold_path in sorted(GOLD_DIR.glob("*.gold.json")):
        gold = json.loads(gold_path.read_text())
        check(f"gold: {gold['reportId']} ({gold['behaviourId']})",
              {"requiredFields": gold["requiredFields"], "policyFields": gold["policyFields"], "behaviourId": gold["behaviourId"]},
              "supported", results)

    print("\n=== 3. Live Phase 2 extractor output, fed straight into Stage 3 ===")
    print("    (expected status is now computed per-run from the same structural-dependency check")
    print("     Stage 3 uses, NOT hardcoded 'supported' — see the round-2 correction note in")
    print("     observability_checker.py: real extractor output can legitimately be incomplete,")
    print("     and a hardcoded 'always supported' expectation would hide exactly the cases this")
    print("     fix exists to catch, rather than test them.)")
    for gold_path in sorted(GOLD_DIR.glob("*.gold.json")):
        gold = json.loads(gold_path.read_text())
        report_text = (REPO_ROOT / gold["reportFile"]).read_text(encoding="utf-8")

        classical_result = classical_extractor.extract(report_text)
        classical_spec = {
            "requiredFields": classical_result.requiredFields,
            "policyFields": classical_result.policyFields,
            "behaviourId": classical_result.behaviourId,
        }
        check(f"classical -> {gold['reportId']}", classical_spec, expected_status(classical_spec), results)

        transformer_result = transformer_extractor.extract(report_text)
        transformer_spec = {
            "requiredFields": transformer_result["requiredFields"],
            "policyFields": transformer_result["policyFields"],
            "behaviourId": transformer_result["behaviourId"],
        }
        check(f"transformer -> {gold['reportId']}", transformer_spec, expected_status(transformer_spec), results)

    out_path = REPO_ROOT / "experiments" / "results" / "phase3_validation_eval.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
