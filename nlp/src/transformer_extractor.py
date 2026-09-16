"""Transformer-based extractor: a pretrained LLM (Groq, openai/gpt-oss-120b)
prompted to fill a fixed slot schema, constrained to the same controlled
vocabulary the classical extractor uses (schema_fields.py) so the two are
scored on equal footing.

This is not a from-scratch trained model. With 10 training reports, no
extraction model trained from scratch would generalize meaningfully — a
"trained Transformer" claim on that little data would be closer to
memorization than science, and this project's whole premise is not
fabricating results. Using a pretrained Transformer for structured, in-context
extraction is a standard and increasingly common real technique, not a
downgrade dressed up as one; growing this into a genuinely fine-tuned model
is realistic future work once report volume justifies it (see
docs/data-splits.md), not a claim made here.

Requires GROQ_API_KEY (see .env, gitignored) — same setup as
experiments/baselines/direct_llm/generate.py.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from schema_fields import BEHAVIOUR_IDS, LOG_FIELDS, POLICY_FIELDS

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")
DEFAULT_MODEL = os.environ.get("SENTINEL_FORGE_LLM_MODEL", "openai/gpt-oss-120b")

PROMPT_TEMPLATE = """You are extracting a structured behaviour specification from a security \
threat report, for a detection-rule compiler.

Available log fields (from the authentication log schema): {log_fields}
Available policy reference fields (a separate identity/policy table, not part of the log): {policy_fields}

Read the report below and output ONLY a JSON object with these keys:
- "behaviourId": chosen from EXACTLY this list, whichever one the report describes: {behaviour_ids}
- "requiredFields": array of log field names, chosen ONLY from the list above, that a \
detection rule for this EXACT behaviour must read to work. Do not include a field just \
because it is mentioned in the report — only include it if the report says or clearly \
implies it is part of the actual triggering condition. If the report explicitly says a \
field is incidental or "not part of" the behaviour, you must leave it out.
- "policyFields": array of policy reference field names, chosen ONLY from the list above, \
required if any, else an empty array.
- "threshold": an object of any numeric thresholds confirmed in the report (for example \
{{"failureCount": 5}} or {{"distinctAccountCount": 4}}), or null if there is no numeric \
threshold.
- "timeWindow": {{"amount": <number>, "unit": "minutes"}} if the behaviour is time-windowed, \
or null if it is not.
- "provenance": an object mapping EACH field name you listed in requiredFields and \
policyFields to the exact substring, copied verbatim from the report text below, that \
justifies including it. Copy the substring exactly — do not paraphrase or summarize it.

Report:
---
{report_text}
---

Output only the JSON object, no explanation, no markdown code fence."""


def build_prompt(report_text: str) -> str:
    return PROMPT_TEMPLATE.format(
        log_fields=", ".join(LOG_FIELDS),
        policy_fields=", ".join(POLICY_FIELDS),
        behaviour_ids=", ".join(BEHAVIOUR_IDS),
        report_text=report_text,
    )


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def call_model(prompt: str, model: str = DEFAULT_MODEL) -> str:
    from groq import Groq

    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit("GROQ_API_KEY is not set — see .env (gitignored).")

    client = Groq()
    response = client.chat.completions.create(
        model=model,
        max_tokens=1500,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def extract(report_text: str, model: str = DEFAULT_MODEL) -> dict:
    prompt = build_prompt(report_text)
    raw = call_model(prompt, model)
    cleaned = _strip_fence(raw)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to grabbing the first {...} block if the model added
        # any stray text despite instructions — real models sometimes do.
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ValueError(f"Model output was not parseable JSON: {raw!r}")
        result = json.loads(match.group(0))

    # Defend the controlled vocabulary the same way the classical extractor
    # does — a model can and occasionally will invent a field name.
    result["requiredFields"] = [f for f in result.get("requiredFields", []) if f in LOG_FIELDS]
    result["policyFields"] = [f for f in result.get("policyFields", []) if f in POLICY_FIELDS]
    if result.get("behaviourId") not in BEHAVIOUR_IDS:
        result["behaviourId"] = "unrecognized:" + str(result.get("behaviourId"))

    # Verify every claimed quote actually appears in the source text —
    # LLMs readily produce a plausible-sounding but non-literal "quote," and
    # an unverified span is worse than none: it looks like evidence without
    # being evidence. Anything that doesn't match exactly is flagged, never
    # silently trusted or silently dropped.
    #
    # Matching is whitespace-tolerant: these reports are hand-wrapped
    # markdown, so a continuous phrase like "sign-in attempts" is stored in
    # the file as "sign-in\nattempts". A model quoting it with a plain space
    # is quoting it correctly in every sense that matters — penalizing that
    # would be scoring markdown formatting, not extraction quality.
    raw_provenance = result.get("provenance", {}) or {}
    verified_provenance = {}
    for field_name, quote in raw_provenance.items():
        if not isinstance(quote, str):
            continue
        pattern = re.escape(quote)
        pattern = re.sub(r"(?:\\ )+", r"\\s+", pattern)
        match = re.search(pattern, report_text)
        if match is None:
            verified_provenance[field_name] = {"verified": False, "claimedQuote": quote}
        else:
            verified_provenance[field_name] = {
                "verified": True,
                "charStart": match.start(),
                "charEnd": match.end(),
                "text": report_text[match.start():match.end()],
            }
    result["provenance"] = verified_provenance
    return result
