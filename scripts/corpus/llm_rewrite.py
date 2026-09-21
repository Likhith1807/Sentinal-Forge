"""Guarded LLM rewrite: restyle a rendered report without being allowed to change its gold.

The rendered (template) report is the source of truth. The model may rewrite everything except
the phrases the gold depends on, which must survive verbatim; the rewrite is accepted only if:

  * every gold-bearing phrase (threshold expression such as "at least 5", window expression such
    as "2 minutes", each required/excluded/unavailable field mention) is still present, so every
    span can be relocated by exact search;
  * it introduces no digit sequence that the original did not contain (no invented numbers);
  * every account/host/ticket identifier from the original is still present;
  * it does not announce that the behaviour is unsupported (that would leak the label into text);
  * its length is sane.

What this does NOT guarantee: that the prose around the preserved phrases means the same thing. That
is why rewritten reports are tagged ``tier: "llm-rewrite"``, are unverified, and the human
verification sample (docs/corpus.md) is stratified across both tiers.
"""
from __future__ import annotations

import json
import os
import random
import re
import time

STYLE_DESCRIPTIONS = {
    "email": "an internal email from a SOC analyst to the detection-engineering team",
    "intel-brief": "a short threat-intelligence brief written as two compact paragraphs",
    "incident-review": "a blameless post-incident review section written in flowing prose",
    "chat-message": "a terse message in a security-team chat channel, informal but clear",
    "runbook": "a runbook entry with a short numbered list of steps",
}
GOLD_LABELS = {"threshold", "window_amount", "window_unit", "required_field", "excluded_field", "unavailable_field"}
_ID_TOKEN = re.compile(r"\b[A-Za-z]+(?:-[A-Za-z]+)*-\d+\b")      # hostnames and ticket ids, e.g. EXT-VPN-12, INC-24936
LEAK_PATTERN =re.compile(r"unsupported|out[- ]of[- ]scope|not supported|cannot be observed|outside (?:the|what)", re.I)
DEFAULT_MODEL = os.environ.get("SENTINEL_FORGE_LLM_MODEL", "openai/gpt-oss-120b")

PROMPT = """Rewrite the security incident report below as {style}.

Hard rules:
1. Include each of the quoted phrases below EXACTLY as written, character for character, inside your rewrite. They are \
spans an evaluation depends on, so do not paraphrase, reorder, pluralise or re-case them; adapt the words AROUND a \
phrase instead of the phrase itself.
{phrases}
2. Do not add any new numbers, dates, times, names, hostnames, account names or identifiers. Every account name, \
hostname and ticket identifier that appears in the original must still appear.
3. Keep the meaning: what triggers the detection, what data it needs, and anything the original says is incidental \
or not part of the pattern must stay clear.
4. Do not say whether the behaviour is supported, observable or feasible for any system.
5. Write 80 to 250 words. Output only the rewritten report, with no preamble.

Original:
---
{text}
---"""


def keep_phrases(spans: list[dict], thr_expr: str | None, win_expr: str | None) -> list[str]:
    """The verbatim phrases the gold depends on (whole expressions for threshold/window)."""
    phrases = [p for p in (thr_expr, win_expr) if p]
    for span in spans:
        if span["label"] in ("required_field", "excluded_field", "unavailable_field"):
            phrases.append(span["text"])
    return list(dict.fromkeys(phrases))


def _identifiers(entities: dict, meta: dict) -> list[str]:
    """Account and host names that must survive a rewrite (only enforced if they appear in the original).

    Ticket ids and analyst names are deliberately NOT required: they are distractors that carry no gold, and
    demanding them rejected otherwise-faithful rewrites (measured: it was the second most common rejection).
    """
    values: list = [v for k, v in entities.items() if k != "topic"]
    flat = [x for v in values for x in (v if isinstance(v, list) else [v])]
    return [x for x in flat if isinstance(x, str) and len(x) > 2]


def validate_rewrite(original: str, rewritten: str, spans: list[dict], phrases: list[str], identifiers: list[str],
                     thr_expr: str | None, win_expr: str | None, supported: bool) -> tuple[list[dict] | None, str]:
    """Return ``(relocated_spans, "")`` if acceptable, else ``(None, reason)``."""
    words = len(rewritten.split())
    if not 50 <= words <= 500:
        return None, f"length {words} words out of range"
    optional = {sp["text"] for sp in spans if sp["label"] == "excluded_field"}
    missing = [p for p in phrases if p not in rewritten and p not in optional]
    if missing:
        return None, f"missing verbatim phrases: {missing[:3]}"
    # Numbered-list markers ("1." at a line start) are formatting, not invented facts.
    body = re.sub(r"(?m)^\s*\d+[.)]\s+", "", rewritten)
    new_numbers = set(re.findall(r"\d+", body)) - set(re.findall(r"\d+", original))
    if new_numbers:
        return None, f"invented numbers: {sorted(new_numbers)[:5]}"
    # Dropping a hostname/ticket is harmless (no gold depends on it); INVENTING one is not.
    invented = set(_ID_TOKEN.findall(rewritten)) - set(_ID_TOKEN.findall(original))
    if invented:
        return None, f"invented identifiers: {sorted(invented)[:3]}"
    if not supported and LEAK_PATTERN.search(rewritten):
        return None, "announces the behaviour is unsupported (label leak)"

    relocated: list[dict] = []
    for span in spans:
        if span["label"] not in GOLD_LABELS:
            continue
        start = _locate(span, rewritten, thr_expr, win_expr)
        if start is None and span["label"] == "excluded_field":
            continue                    # the incidental-field note was dropped; build() removes it from the gold
        if start is None:
            return None, f"could not relocate span {span['label']}={span['text']!r}"
        relocated.append({**span, "start": start, "end": start + len(span["text"])})
    for span in relocated:
        assert rewritten[span["start"]:span["end"]] == span["text"]
    return relocated, ""


def _locate(span: dict, text: str, thr_expr: str | None, win_expr: str | None) -> int | None:
    label = span["label"]
    if label == "threshold" and thr_expr:
        idx = text.find(thr_expr)
        return None if idx < 0 else idx + thr_expr.index(span["text"])
    if label in ("window_amount", "window_unit") and win_expr:
        idx = text.find(win_expr)
        if idx < 0:
            return None
        return idx + (0 if label == "window_amount" else len(win_expr) - len(span["text"]))
    idx = text.find(span["text"])
    return None if idx < 0 else idx


REASONING_EFFORT = "low"     # a rewrite needs no deep reasoning; "default" runs (cache) used the provider default


def call_model(client, prompt: str, model: str, temperature: float, attempts: int = 8) -> str:
    """One chat completion with exponential backoff on rate limits / transient errors.

    Rate limits here are per-minute token caps, so the backoff must be able to outlast a full minute window.
    """
    delay = 5.0
    for attempt in range(attempts):
        try:
            response = client.chat.completions.create(
                model=model, temperature=temperature, max_tokens=1200, reasoning_effort=REASONING_EFFORT,
                messages=[{"role": "user", "content": prompt}])
            return response.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 - provider errors vary; classify by message
            transient = any(t in str(exc).lower() for t in ("429", "rate", "timeout", "503", "502", "overloaded"))
            if not transient or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60)
    return ""


def make_client():
    from dotenv import load_dotenv
    from groq import Groq
    from .vocab import REPO_ROOT
    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit("GROQ_API_KEY is not set (see .env, git-ignored).")
    return Groq()


def rewrite(client, *, text: str, spans: list[dict], thr_expr, win_expr, entities: dict, meta: dict,
            supported: bool, style: str, rng: random.Random, model: str = DEFAULT_MODEL,
            max_tries: int = 4) -> dict:
    """Try up to ``max_tries`` times; return ``{ok, text, spans, tries, reason, model, style}``."""
    phrases = keep_phrases(spans, thr_expr, win_expr)
    identifiers = _identifiers(entities, meta)
    reason = ""
    for attempt in range(1, max_tries + 1):
        prompt = PROMPT.format(style=STYLE_DESCRIPTIONS[style], phrases="\n".join(f'   - "{p}"' for p in phrases), text=text)
        try:
            candidate = call_model(client, prompt, model, temperature=0.4 + 0.15 * (attempt - 1)).strip()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"api error: {type(exc).__name__}", "tries": attempt, "style": style}
        relocated, reason = validate_rewrite(text, candidate, spans, phrases, identifiers, thr_expr, win_expr, supported)
        if relocated is not None:
            return {"ok": True, "text": candidate, "spans": relocated, "tries": attempt, "reason": "",
                    "model": model, "style": style, "reasoning_effort": REASONING_EFFORT}
    return {"ok": False, "reason": reason, "tries": max_tries, "style": style}
