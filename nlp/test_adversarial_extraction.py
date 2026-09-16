"""Runs both extractors against the 6 adversarial fixtures (3 prompt-injection,
tested separately by test_injection_guard.py, plus 3 non-injection
extraction-robustness traps here) and saves real results. Closes the
broader half of Phase 5's "adversarial test set" item — see
experiments/results/README.md.

Usage:
    python nlp/test_adversarial_extraction.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
import classical_extractor as ce  # noqa: E402
import transformer_extractor as te  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = REPO_ROOT / "compiler" / "test" / "fixtures" / "adversarial"

FIXTURES = [
    "contradictory-threshold",
    "alarming-tone-benign-facts",
    "synonym-substitution",
]


def main() -> None:
    results = []
    for name in FIXTURES:
        text = (FIXTURES_DIR / f"{name}.md").read_text(encoding="utf-8")

        c = ce.extract(text)
        classical_result = {
            "behaviourId": c.behaviourId,
            "requiredFields": c.requiredFields,
            "policyFields": c.policyFields,
            "threshold": c.threshold,
            "timeWindow": c.timeWindow,
        }

        t = te.extract(text)
        transformer_result = {
            "behaviourId": t["behaviourId"],
            "requiredFields": t["requiredFields"],
            "policyFields": t["policyFields"],
            "threshold": t["threshold"],
            "timeWindow": t["timeWindow"],
        }

        results.append({"fixture": name, "classical": classical_result, "transformer": transformer_result})

        print(f"===== {name} =====")
        print(f"  classical:    {classical_result}")
        print(f"  transformer:  {transformer_result}")
        print()

    out_path = REPO_ROOT / "experiments" / "results" / "phase5_adversarial_extraction.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
