"""A single fine-tuned encoder with five task heads, matching corpus_dataset's annotation schema.

    behaviour   6-way classification (5 behaviours + "unsupported"), pooled [CLS]/<s>
    fields      11-way multi-label classification (required + policy fields), pooled
    threshold   start/end token-position extraction (only where a threshold exists)
    more-than   binary classification: is the shown number one less than the true value?
    window      start/end token-position extraction for the window amount
    window-unit 3-way classification (seconds/minutes/hours), pooled

One shared encoder, not five separate models — the tasks share the same reading of the report, and
a single forward pass is what a real extractor needs at inference time anyway.
"""
from __future__ import annotations

import torch
import torch.nn as nn
from transformers import AutoModel

from corpus_dataset import ALL_FIELDS, BEHAVIOUR_LABELS, IGNORE_INDEX, WINDOW_UNITS

N_BEHAVIOUR = len(BEHAVIOUR_LABELS)
N_FIELDS = len(ALL_FIELDS)
N_UNITS = len(WINDOW_UNITS)


class MultiTaskExtractor(nn.Module):
    def __init__(self, encoder_name: str, dropout: float = 0.1):
        super().__init__()
        self.encoder_name = encoder_name
        self.encoder = AutoModel.from_pretrained(encoder_name)
        h = self.encoder.config.hidden_size
        self.dropout = nn.Dropout(dropout)
        self.behaviour_head = nn.Linear(h, N_BEHAVIOUR)
        self.field_head = nn.Linear(h, N_FIELDS)
        self.more_than_head = nn.Linear(h, 1)
        self.unit_head = nn.Linear(h, N_UNITS)
        self.threshold_qa = nn.Linear(h, 2)
        self.window_qa = nn.Linear(h, 2)

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> dict:
        seq = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        pooled = self.dropout(seq[:, 0])
        thr_start, thr_end = self.threshold_qa(seq).unbind(-1)
        win_start, win_end = self.window_qa(seq).unbind(-1)
        # Padding tokens must not win an argmax at inference: push them to -inf.
        pad = (attention_mask == 0)
        thr_start, thr_end = thr_start.masked_fill(pad, -1e9), thr_end.masked_fill(pad, -1e9)
        win_start, win_end = win_start.masked_fill(pad, -1e9), win_end.masked_fill(pad, -1e9)
        return {
            "behaviour_logits": self.behaviour_head(pooled),
            "field_logits": self.field_head(pooled),
            "more_than_logit": self.more_than_head(pooled).squeeze(-1),
            "unit_logits": self.unit_head(pooled),
            "threshold_start_logits": thr_start, "threshold_end_logits": thr_end,
            "window_start_logits": win_start, "window_end_logits": win_end,
        }


def compute_losses(out: dict, batch: dict) -> dict:
    """Per-task losses, each masked to the examples where that supervision applies."""
    ce, bce = nn.functional.cross_entropy, nn.functional.binary_cross_entropy_with_logits
    losses = {
        "behaviour": ce(out["behaviour_logits"], batch["behaviourLabel"]),
        "fields": bce(out["field_logits"], batch["fieldLabels"]),
    }
    has_thr = batch["hasThreshold"].bool()
    if has_thr.any():
        losses["threshold_span"] = 0.5 * (
            ce(out["threshold_start_logits"][has_thr], batch["thresholdStart"][has_thr], ignore_index=IGNORE_INDEX)
            + ce(out["threshold_end_logits"][has_thr], batch["thresholdEnd"][has_thr], ignore_index=IGNORE_INDEX))
        losses["more_than"] = bce(out["more_than_logit"][has_thr], batch["thresholdIsMoreThan"][has_thr])
    has_win = batch["hasWindow"].bool()
    if has_win.any():
        losses["window_span"] = 0.5 * (
            ce(out["window_start_logits"][has_win], batch["windowStart"][has_win], ignore_index=IGNORE_INDEX)
            + ce(out["window_end_logits"][has_win], batch["windowEnd"][has_win], ignore_index=IGNORE_INDEX))
        losses["window_unit"] = ce(out["unit_logits"][has_win], batch["windowUnitLabel"][has_win])
    losses["total"] = sum(losses.values())
    return losses
