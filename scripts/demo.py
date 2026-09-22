"""A fast, offline walkthrough of the report-to-detection path — no Groq API key, no JVM/Spark,
no generated dataset. Runs in seconds, not minutes, by design: it demonstrates the parts of the
pipeline that don't need those (classical extraction, Stage 3 validation) and then shows the
REAL, already-computed results from the parts that do (the Scala/Spark compiler, the fine-tuned
model), rather than faking a live re-run of either — the same "serve the real numbers, don't
pretend to recompute them live" boundary `dashboard/README.md` documents for the dashboard itself.

Usage:
    python scripts/demo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))
sys.path.insert(0, str(REPO_ROOT / "compiler" / "src"))


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> int:
    import classical_extractor
    import observability_checker as stage3

    section("1. Extract a behaviour spec from a real threat report (classical extractor, offline)")
    report_path = REPO_ROOT / "data" / "samples" / "reports" / "login-brute-force-001.md"
    report_text = report_path.read_text(encoding="utf-8")
    print(f"Report: {report_path.relative_to(REPO_ROOT)}")
    result = classical_extractor.extract(report_text)
    print(f"  behaviourId: {result.behaviourId}")
    print(f"  requiredFields: {sorted(result.requiredFields)}")
    print(f"  threshold: {result.threshold}")
    print(f"  timeWindow: {result.timeWindow}")

    section("2. Validate the spec against the real log schema (Stage 3)")
    spec = {"behaviourId": result.behaviourId, "requiredFields": result.requiredFields,
           "policyFields": result.policyFields, "threshold": result.threshold, "timeWindow": result.timeWindow}
    validation = stage3.validate(spec)
    print(f"  status: {validation.status}")
    if validation.missingFields:
        print(f"  missingFields: {validation.missingFields}")

    section("3. Watch the verdict degrade live (simulate a required field becoming unavailable)")
    unavailable = {result.requiredFields[0]} if result.requiredFields else set()
    degraded = stage3.validate(spec, unavailable_fields=unavailable)
    print(f"  removing {unavailable or '(none required)'} -> status: {degraded.status}")
    if degraded.missingFields:
        print(f"  missingFields: {degraded.missingFields}")

    section("4. Real, already-computed results (not re-run live - see docs/spec/detection-semantics.md)")
    replay = json.loads((REPO_ROOT / "experiments" / "results" / "phase4_replay_check.json").read_text(encoding="utf-8"))
    passed = sum(1 for r in replay if r["pass"])
    print(f"  ReplayCheck (real Spark, real replay labels): {passed}/{len(replay)} scenarios passed")
    scale = json.loads((REPO_ROOT / "experiments" / "results" / "phaseB_generated_dataset_check.json").read_text(encoding="utf-8"))
    print(f"  GeneratedDataCheck (real 36.7M-event dataset): overallPass={scale['overallPass']}, "
         f"{sum(b['passed'] for b in scale['behaviours'])}/{sum(b['labels'] for b in scale['behaviours'])} labels")
    corpus = json.loads((REPO_ROOT / "experiments" / "results" / "phaseC_corpus_comparison.json").read_text(encoding="utf-8"))
    ft = corpus["systems"]["fine-tuned"]
    print(f"  Fine-tuned extractor (real gpt-oss-120b comparison, n={ft['scored']}): "
         f"behaviour accuracy {ft['behaviourAccuracy']:.3f}, field F1 {ft['fieldF1']:.3f}")

    section("Done")
    print("This ran entirely offline. For the live dashboard: docker compose up --build, then")
    print("open http://localhost:8000. For the full Scala/Spark checks: see")
    print("docs/spec/stage4-scala-toolchain.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
