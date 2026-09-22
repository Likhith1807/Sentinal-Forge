"""Annotation schema and tensorization for the fine-tuned extractor (Phase C).

Every label here is **derived from the report corpus's own gold** (`data/corpus/gold/*.gold.json`,
built by `scripts/corpus/build.py`), not separately hand-annotated — the corpus generator already
produces exact behaviour, field, threshold and window labels with character-offset provenance
(`docs/corpus.md`). This module is the "define the annotation schema" step: it names each label,
its type, and exactly how it is read off the gold JSON, and turns that into tensors a model can
train on. It does not invent new labels.

## Schema

| Label | Type | Present when | Source in gold |
|---|---|---|---|
| `behaviourLabel` | 1-of-6 class (5 behaviours + `unsupported`) | always | `behaviourId` (`null` -> `unsupported`) |
| `fieldLabels` | 11-dim multi-hot over `schema_fields.ALL_FIELDS` | always | `requiredFields` union `policyFields` |
| `thresholdValue` | integer | B1/B2/B3 only | `threshold`'s single value |
| `thresholdSpan` | character range in the report text | same | the `threshold` span |
| `thresholdIsMoreThan` | binary | same | `thresholdForm == "more-than"` (the one phrasing where the *displayed* number is the value minus one; every other phrasing displays the true value) |
| `windowAmountValue` | integer | same | `timeWindow.amount` |
| `windowAmountSpan` | character range | same | the `window_amount` span |
| `windowUnitLabel` | 1-of-3 class (seconds/minutes/hours) | same | `timeWindow.unit` |

B4/B5 (`service-account-interactive-auth`, `mfa-bypass-on-required-account`) and `unsupported`
reports have no threshold or window; those examples are excluded from the corresponding loss terms
(`hasThreshold`/`hasWindow` masks), not padded with a fake value.

Character spans are converted to token index ranges with the tokenizer's fast offset mapping. A
span that falls outside `max_length` after truncation is dropped from supervision for that example
(counted and reported by `load_split`, never silently ignored).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import torch
from torch.utils.data import Dataset

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "data" / "corpus"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_fields import ALL_FIELDS, BEHAVIOUR_IDS  # noqa: E402

UNSUPPORTED = "unsupported"
BEHAVIOUR_LABELS = list(BEHAVIOUR_IDS) + [UNSUPPORTED]
BEHAVIOUR_TO_IDX = {b: i for i, b in enumerate(BEHAVIOUR_LABELS)}
FIELD_TO_IDX = {f: i for i, f in enumerate(ALL_FIELDS)}
WINDOW_UNITS = ["seconds", "minutes", "hours"]
UNIT_TO_IDX = {u: i for i, u in enumerate(WINDOW_UNITS)}
# The bridge's accepted threshold key per behaviour (compiler/src/spec_bridge.EXPECTED_THRESHOLD_KEYS);
# duplicated as a plain literal here rather than imported, since that module lives outside nlp/'s
# import path and this is a fixed, closed mapping the same as the recipe shapes it comes from.
THRESHOLD_KEY = {
    "repeated-failed-login-then-success": "failureCount",
    "password-spray-across-accounts": "distinctAccountCount",
    "concurrent-sessions-different-hosts": "successCount",
}


@dataclass
class Example:
    reportId: str
    text: str
    tier: str
    style: str
    behaviourLabel: int
    fieldLabels: list          # 11 floats, 0/1
    hasThreshold: bool
    thresholdValue: int | None
    thresholdCharSpan: tuple | None
    thresholdIsMoreThan: bool
    hasWindow: bool
    windowAmountValue: int | None
    windowAmountCharSpan: tuple | None
    windowUnitLabel: int | None


def _span(gold: dict, label: str) -> tuple | None:
    for s in gold["spans"]:
        if s["label"] == label:
            return (s["start"], s["end"])
    return None


def load_examples(corpus_dir: Path = CORPUS_DIR, split: str | None = None) -> list[Example]:
    """Load every corpus report (optionally filtered to one split) as an `Example`."""
    splits = json.loads((corpus_dir / "splits.json").read_text(encoding="utf-8"))["reports"]
    examples = []
    for gold_path in sorted((corpus_dir / "gold").glob("*.gold.json")):
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        report_id = gold["reportId"]
        if split is not None and splits.get(report_id) != split:
            continue
        text = (corpus_dir / "reports" / f"{report_id}.md").read_text(encoding="utf-8")
        behaviour_label = BEHAVIOUR_TO_IDX[gold["behaviourId"]] if gold["supported"] else BEHAVIOUR_TO_IDX[UNSUPPORTED]
        field_labels = [0.0] * len(ALL_FIELDS)
        for f in gold.get("requiredFields", []) + gold.get("policyFields", []):
            field_labels[FIELD_TO_IDX[f]] = 1.0

        threshold, window = gold.get("threshold"), gold.get("timeWindow")
        has_threshold, has_window = bool(threshold), bool(window)
        examples.append(Example(
            reportId=report_id, text=text, tier=gold["tier"], style=gold["style"],
            behaviourLabel=behaviour_label, fieldLabels=field_labels,
            hasThreshold=has_threshold,
            thresholdValue=next(iter(threshold.values())) if has_threshold else None,
            thresholdCharSpan=_span(gold, "threshold") if has_threshold else None,
            thresholdIsMoreThan=gold.get("thresholdForm") == "more-than",
            hasWindow=has_window,
            windowAmountValue=window["amount"] if has_window else None,
            windowAmountCharSpan=_span(gold, "window_amount") if has_window else None,
            windowUnitLabel=UNIT_TO_IDX[window["unit"]] if has_window else None,
        ))
    return examples


IGNORE_INDEX = -100   # standard convention: excluded from CrossEntropyLoss by construction


class CorpusExtractionDataset(Dataset):
    """Tokenizes `Example`s for the multi-task model. Reports token-level span drops via `.dropped`."""

    def __init__(self, examples: list[Example], tokenizer, max_length: int = 256):
        self.examples = examples
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.dropped = {"threshold": 0, "window": 0}
        self._encodings = [self._encode(e) for e in examples]

    def _char_to_token_range(self, offsets: torch.Tensor, span: tuple) -> tuple | None:
        start_char, end_char = span
        start_tok = end_tok = None
        for i, (s, e) in enumerate(offsets.tolist()):
            if s == e == 0:            # special token (offset (0,0))
                continue
            if start_tok is None and s <= start_char < e:
                start_tok = i
            if s < end_char <= e:
                end_tok = i
        return (start_tok, end_tok) if start_tok is not None and end_tok is not None else None

    def _encode(self, ex: Example) -> dict:
        enc = self.tokenizer(ex.text, truncation=True, max_length=self.max_length,
                             padding="max_length", return_offsets_mapping=True, return_tensors="pt")
        offsets = enc["offset_mapping"][0]
        item = {
            "input_ids": enc["input_ids"][0], "attention_mask": enc["attention_mask"][0],
            "behaviourLabel": torch.tensor(ex.behaviourLabel, dtype=torch.long),
            "fieldLabels": torch.tensor(ex.fieldLabels, dtype=torch.float),
            "hasThreshold": torch.tensor(float(ex.hasThreshold)),
            "hasWindow": torch.tensor(float(ex.hasWindow)),
            "thresholdIsMoreThan": torch.tensor(float(ex.thresholdIsMoreThan)),
            "thresholdStart": torch.tensor(IGNORE_INDEX, dtype=torch.long),
            "thresholdEnd": torch.tensor(IGNORE_INDEX, dtype=torch.long),
            "windowStart": torch.tensor(IGNORE_INDEX, dtype=torch.long),
            "windowEnd": torch.tensor(IGNORE_INDEX, dtype=torch.long),
            "windowUnitLabel": torch.tensor(ex.windowUnitLabel if ex.windowUnitLabel is not None else IGNORE_INDEX, dtype=torch.long),
        }
        if ex.hasThreshold:
            rng = self._char_to_token_range(offsets, ex.thresholdCharSpan)
            if rng is None:
                self.dropped["threshold"] += 1
                item["hasThreshold"] = torch.tensor(0.0)
            else:
                item["thresholdStart"], item["thresholdEnd"] = torch.tensor(rng[0]), torch.tensor(rng[1])
        if ex.hasWindow:
            rng = self._char_to_token_range(offsets, ex.windowAmountCharSpan)
            if rng is None:
                self.dropped["window"] += 1
                item["hasWindow"] = torch.tensor(0.0)
                item["windowUnitLabel"] = torch.tensor(IGNORE_INDEX, dtype=torch.long)
            else:
                item["windowStart"], item["windowEnd"] = torch.tensor(rng[0]), torch.tensor(rng[1])
        return item

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, idx: int) -> dict:
        return self._encodings[idx]


def load_split(split: str, tokenizer, corpus_dir: Path = CORPUS_DIR, max_length: int = 256) -> CorpusExtractionDataset:
    examples = load_examples(corpus_dir, split)
    if not examples:
        raise ValueError(f"No corpus examples found for split={split!r} under {corpus_dir}")
    ds = CorpusExtractionDataset(examples, tokenizer, max_length)
    if ds.dropped["threshold"] or ds.dropped["window"]:
        print(f"[corpus_dataset] split={split}: dropped {ds.dropped['threshold']} threshold span(s) and "
              f"{ds.dropped['window']} window span(s) that fell outside max_length={max_length}", file=sys.stderr)
    return ds
