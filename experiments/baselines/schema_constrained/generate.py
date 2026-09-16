"""Schema-constrained generation baseline: extract -> validate (Stage 3) ->
generate code ONLY if supported, using ONLY the validated field list.

This is the capability direct_llm/generate.py structurally lacks: given a
report Stage 3 rejects, this baseline emits a rejection artifact instead of
guessing. That's the entire point of comparing it against direct-LLM
generation in Phase 5 — same underlying model, same report, different
outcome, because one of them checks its work before answering.

Usage:
    python experiments/baselines/schema_constrained/generate.py \
        --report data/samples/reports/password-spray-001.md \
        --out experiments/baselines/schema_constrained/generated/password-spray-001.scala
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))
sys.path.insert(0, str(REPO_ROOT / "compiler" / "src"))

import transformer_extractor  # noqa: E402
import observability_checker as stage3  # noqa: E402

PROMPT_TEMPLATE = Path(__file__).with_name("prompt_template.md")


def build_prompt(report_text: str, validated_fields: list[str]) -> str:
    template = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    block = template.split("```", 2)[1]
    policy_param = ""
    if any(f.startswith("policy.") for f in validated_fields):
        policy_param = ", and a second DataFrame `policy` of account policy records,"
    return block.format(
        validated_fields="\n".join(f"- {f}" for f in validated_fields),
        policy_param=policy_param,
        report_text=report_text,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.report = args.report.resolve()
    args.out = args.out.resolve()

    report_text = args.report.read_text(encoding="utf-8")

    spec = transformer_extractor.extract(report_text)
    result = stage3.validate(spec)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "report": str(args.report.relative_to(REPO_ROOT)),
        "extractedSpec": {"requiredFields": spec["requiredFields"], "policyFields": spec["policyFields"], "behaviourId": spec["behaviourId"]},
        "stage3Status": result.status,
        "stage3Notes": result.notes,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }

    if result.status != "supported":
        rejection_path = args.out.with_suffix(".rejected.json")
        rejection_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"REJECTED by Stage 3 — wrote {rejection_path}, no code generated.")
        for note in result.notes:
            print(f"  - {note}")
        return

    validated_fields = spec["requiredFields"] + spec["policyFields"]
    prompt = build_prompt(report_text, validated_fields)

    from transformer_extractor import call_model, _strip_fence
    generated_code = _strip_fence(call_model(prompt))

    args.out.write_text(generated_code, encoding="utf-8")
    meta_path = args.out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"SUPPORTED — wrote {args.out} and {meta_path}")


if __name__ == "__main__":
    main()
