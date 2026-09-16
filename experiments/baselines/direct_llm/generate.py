"""Direct-LLM-generation baseline: report + schema -> Scala rule, with no
schema validation and no evidence-citation step afterward. See
prompt_template.md for exactly what the model is given, and its header
comment for why this baseline is built to be weak on purpose — the point of
running it is to measure how often "just ask an LLM" quietly produces a
rule that references an unsupported field (like source_ip) or an
unconfirmed threshold, not to make it look bad by construction.

Runs against Groq's OpenAI-compatible chat completions API. Needs
GROQ_API_KEY set (in the environment, or in a .env file at the repo root —
see .gitignore, which excludes .env from version control).

Usage:
    python experiments/baselines/direct_llm/generate.py \
        --report data/samples/reports/password-spray-001.md \
        --out experiments/baselines/direct_llm/generated/password-spray-001.scala
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env")
SCHEMA_DOC = REPO_ROOT / "docs" / "schema" / "authentication-log-schema.md"
PROMPT_TEMPLATE = Path(__file__).with_name("prompt_template.md")
DEFAULT_MODEL = os.environ.get("SENTINEL_FORGE_LLM_MODEL", "openai/gpt-oss-120b")


def build_prompt(report_text: str, schema_text: str) -> str:
    template = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    block = template.split("```", 2)[1]
    return block.format(report_text=report_text, schema_text=schema_text)


def strip_code_fence(text: str) -> str:
    """The prompt asks for code only, but models routinely wrap it in a
    ```scala ... ``` fence anyway. Strip that wrapper (only), never touch
    anything else about the model's output — this is packaging cleanup,
    not a correctness fix."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening fence (with optional language tag)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip() + "\n"


def call_model(prompt: str, model: str) -> str:
    try:
        from groq import Groq
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise SystemExit(
            "The 'groq' package is required to run this baseline: pip install groq"
        ) from exc

    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit(
            "GROQ_API_KEY is not set. Put it in a .env file at the repo root "
            "(ANTHROPIC/GROQ keys must never be committed — .env is gitignored)."
        )

    client = Groq()  # reads GROQ_API_KEY from the environment
    response = client.chat.completions.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    args.report = args.report.resolve()
    args.out = args.out.resolve()

    report_text = args.report.read_text(encoding="utf-8")
    schema_text = SCHEMA_DOC.read_text(encoding="utf-8")
    prompt = build_prompt(report_text, schema_text)

    generated_code = strip_code_fence(call_model(prompt, args.model))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(generated_code, encoding="utf-8")

    log_path = args.out.with_suffix(".meta.json")
    log_path.write_text(
        json.dumps(
            {
                "report": str(args.report.relative_to(REPO_ROOT)),
                "provider": "groq",
                "model": args.model,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "promptTemplate": str(PROMPT_TEMPLATE.relative_to(REPO_ROOT)),
                "schemaProvidedButNotValidated": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {args.out} and {log_path}")


if __name__ == "__main__":
    main()
