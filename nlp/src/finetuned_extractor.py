"""Inference wrapper for a trained MultiTaskExtractor checkpoint.

Exposes the same shape the other two extractors' `extract(report_text)` return
(behaviourId/requiredFields/policyFields/threshold/timeWindow/provenance), so it plugs into
`nlp/src/evaluate.py`'s comparison harness and `scripts/corpus/eval_classical.py`-style scoring
without special-casing.

Unlike the two existing extractors, this one has a trained "unsupported" class: when the model
predicts it, `behaviourId` is `None` (not a guess at one of the five, not a free-text label) —
that is a real answer, "this report is outside what the compiler can act on," not a failure to
extract. Downstream, `compiler/src/observability_checker.validate()` already rejects any
`behaviourId not in BEHAVIOUR_IDS`, so `None` degrades safely without any change there.

Threshold/timeWindow are only ever produced when the predicted behaviour is one that has one
(B1/B2/B3) — the recipe shape decides applicability, the same way `spec_bridge.RECIPE_TEMPLATES`
fixes it downstream, not a separate per-report guess.

**Provenance is reported only for threshold and window** (real character spans from the model's
own predicted token positions). Required/policy fields are predicted as a set (multi-label), with
no per-field span localization in this task decomposition — so no field provenance is fabricated
for them; downstream code must treat their absence here as "not attempted," never as "verified
absent."
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import torch
from transformers import AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_dataset import ALL_FIELDS, BEHAVIOUR_LABELS, THRESHOLD_KEY, WINDOW_UNITS  # noqa: E402
from multitask_model import MultiTaskExtractor  # noqa: E402

DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "roberta-base"
UNSUPPORTED_IDX = BEHAVIOUR_LABELS.index("unsupported")

# Mirrors scripts/corpus/vocab.NUMBER_WORDS (the closed vocabulary the corpus generator writes
# numbers from). Duplicated deliberately: this extractor must be usable without importing the
# corpus-authoring package, which is a dev/eval-data tool, not a runtime dependency.
_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
                 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 15: "fifteen", 20: "twenty", 30: "thirty"}
_WORD_TO_NUMBER = {w: n for n, w in _NUMBER_WORDS.items()}


def _parse_number(text: str) -> int | None:
    text = text.strip().lower().rstrip("+")
    if text.isdigit():
        return int(text)
    return _WORD_TO_NUMBER.get(text)


_UNIT_AFTER = re.compile(r"^(\s*[-‐-―]?\s*)(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE)


def unit_after(text: str, pos: int) -> str | None:
    """The time unit written immediately after character `pos` ("90-second", "5 minutes"), else None."""
    m = _UNIT_AFTER.match(text[pos:pos + 24])
    if not m:
        return None
    w = m.group(2).lower()
    return "seconds" if w.startswith("sec") else "minutes" if w.startswith("min") else "hours"


def _unit_span_len(text: str, pos: int) -> int:
    m = _UNIT_AFTER.match(text[pos:pos + 24])
    return m.end() if m else 0


@dataclass
class FinetunedExtractionResult:
    behaviourId: str | None
    requiredFields: list = field(default_factory=list)
    policyFields: list = field(default_factory=list)
    excludedFields: list = field(default_factory=list)   # never populated; see module docstring
    threshold: dict | None = None
    timeWindow: dict | None = None
    provenance: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)              # decoded logits/spans, for inspection


_cache: dict[str, tuple] = {}


def load_model(model_dir: str | Path = DEFAULT_MODEL_DIR, device: str | None = None):
    model_dir = Path(model_dir)
    key = str(model_dir)
    if key in _cache:
        return _cache[key]
    device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    config = json.loads((model_dir / "run_config.json").read_text(encoding="utf-8"))
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = MultiTaskExtractor(config["encoder"])
    model.load_state_dict(torch.load(model_dir / "model.pt", map_location=device))
    model.to(device).eval()
    _cache[key] = (tokenizer, model, device, config)
    return _cache[key]


def _span_text_and_prov(text: str, offsets, start_tok: int, end_tok: int) -> tuple[str, dict]:
    char_start, char_end = offsets[start_tok][0].item(), offsets[end_tok][1].item()
    span_text = text[char_start:char_end]
    return span_text, {"charStart": char_start, "charEnd": char_end, "text": span_text}


def extract(report_text: str, model_dir: str | Path = DEFAULT_MODEL_DIR, device: str | None = None) -> FinetunedExtractionResult:
    tokenizer, model, dev, config = load_model(model_dir, device)
    enc = tokenizer(report_text, truncation=True, max_length=config.get("max_length", 256),
                    return_offsets_mapping=True, return_tensors="pt")
    offsets = enc.pop("offset_mapping")[0]
    enc = {k: v.to(dev) for k, v in enc.items()}
    with torch.no_grad():
        out = model(enc["input_ids"], enc["attention_mask"])

    behaviour_probs = torch.softmax(out["behaviour_logits"], dim=-1)[0]
    behaviour_idx = behaviour_probs.argmax(-1).item()
    behaviour_confidence = behaviour_probs[behaviour_idx].item()
    field_flags = (torch.sigmoid(out["field_logits"])[0] > 0.5).tolist()
    fields = [f for f, flag in zip(ALL_FIELDS, field_flags) if flag]
    # Kept as "policy.<name>" (schema_fields.POLICY_FIELDS' own naming) so downstream code that
    # consumes policyFields gets exactly the form it already expects.
    required = [f for f in fields if not f.startswith("policy.")]
    policy = [f for f in fields if f.startswith("policy.")]

    raw = {"behaviourIdx": behaviour_idx, "fieldFlags": field_flags, "behaviourConfidence": behaviour_confidence}
    if behaviour_idx == UNSUPPORTED_IDX:
        return FinetunedExtractionResult(behaviourId=None, requiredFields=required, policyFields=policy, raw=raw)

    behaviour_id = BEHAVIOUR_LABELS[behaviour_idx]
    threshold = time_window = None
    provenance: dict = {}

    if behaviour_id in THRESHOLD_KEY:
        t_start = out["threshold_start_logits"].argmax(-1).item()
        t_end = out["threshold_end_logits"].argmax(-1).item()
        if t_end >= t_start:
            span_text, prov = _span_text_and_prov(report_text, offsets, t_start, t_end)
            value = _parse_number(span_text)
            more_than = torch.sigmoid(out["more_than_logit"]).item() > 0.5
            if value is not None:
                threshold = {THRESHOLD_KEY[behaviour_id]: value + 1 if more_than else value}
                provenance["threshold"] = prov
            raw["thresholdSpanText"] = span_text
            raw["moreThan"] = more_than

        w_start = out["window_start_logits"].argmax(-1).item()
        w_end = out["window_end_logits"].argmax(-1).item()
        if w_end >= w_start:
            span_text, prov = _span_text_and_prov(report_text, offsets, w_start, w_end)
            amount = _parse_number(span_text)
            # The unit is READ FROM THE TEXT beside the amount span, never taken from the separate unit head:
            # that head answered "minutes" for "90-second" and "600 seconds" (audit, 2026-09), compiling a
            # 60x-too-long window with no sign of trouble. The head's answer is kept only for diagnostics.
            unit_head = WINDOW_UNITS[out["unit_logits"].argmax(-1).item()]
            unit = unit_after(report_text, prov["charEnd"])
            raw["unitHead"] = unit_head
            raw["unitFromText"] = unit
            if amount is not None and unit is not None:
                time_window = {"amount": amount, "unit": unit}
                provenance["timeWindow"] = {**prov, "charEnd": prov["charEnd"] + _unit_span_len(report_text, prov["charEnd"])}
            raw["windowSpanText"] = span_text

    return FinetunedExtractionResult(behaviourId=behaviour_id, requiredFields=required, policyFields=policy,
                                     threshold=threshold, timeWindow=time_window, provenance=provenance, raw=raw)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    ap.add_argument("report_file")
    args = ap.parse_args()
    text = Path(args.report_file).read_text(encoding="utf-8")
    result = extract(text, args.model_dir)
    print(json.dumps({k: v for k, v in result.__dict__.items() if k != "raw"}, indent=2))
