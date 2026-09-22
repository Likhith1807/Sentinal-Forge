"""Shared decode (logits -> predicted labels) and scoring, used by both training-time dev
evaluation and the standalone test-set evaluator, so the two can never silently disagree on
what "correct" means.
"""
from __future__ import annotations

import torch

from corpus_dataset import BEHAVIOUR_LABELS


def decode_batch(source: dict, is_gold: bool = False) -> list[dict]:
    """From either a gold batch (dataset collation) or a model's raw `out` dict, produce one
    plain-Python prediction/gold dict per example: behaviourIdx, fields (0/1 list), hasThreshold,
    thresholdStart/End (token indices), thresholdIsMoreThan, hasWindow, windowStart/End, windowUnitIdx.
    """
    if is_gold:
        n = source["behaviourLabel"].shape[0]
        return [{
            "behaviourIdx": source["behaviourLabel"][i].item(),
            "fields": (source["fieldLabels"][i] > 0.5).tolist(),
            "hasThreshold": bool(source["hasThreshold"][i].item()),
            "thresholdStart": source["thresholdStart"][i].item(), "thresholdEnd": source["thresholdEnd"][i].item(),
            "thresholdIsMoreThan": bool(source["thresholdIsMoreThan"][i].item()),
            "hasWindow": bool(source["hasWindow"][i].item()),
            "windowStart": source["windowStart"][i].item(), "windowEnd": source["windowEnd"][i].item(),
            "windowUnitIdx": source["windowUnitLabel"][i].item(),
        } for i in range(n)]

    behaviour_idx = source["behaviour_logits"].argmax(-1)
    fields = (torch.sigmoid(source["field_logits"]) > 0.5)
    more_than = torch.sigmoid(source["more_than_logit"]) > 0.5
    unit_idx = source["unit_logits"].argmax(-1)
    thr_start, thr_end = source["threshold_start_logits"].argmax(-1), source["threshold_end_logits"].argmax(-1)
    win_start, win_end = source["window_start_logits"].argmax(-1), source["window_end_logits"].argmax(-1)
    unsupported_idx = BEHAVIOUR_LABELS.index("unsupported")
    n = behaviour_idx.shape[0]
    return [{
        "behaviourIdx": behaviour_idx[i].item(),
        "fields": fields[i].tolist(),
        # A predicted "unsupported" behaviour has no threshold/window by construction (see
        # corpus_dataset's schema table: only B1/B2/B3 ever carry one) — the recipe shape decides
        # applicability, exactly as spec_bridge.RECIPE_TEMPLATES fixes it downstream of extraction,
        # not a separate per-example guess.
        "hasThreshold": behaviour_idx[i].item() != unsupported_idx,
        "thresholdStart": thr_start[i].item(), "thresholdEnd": thr_end[i].item(),
        "thresholdIsMoreThan": bool(more_than[i].item()),
        "hasWindow": behaviour_idx[i].item() != unsupported_idx,
        "windowStart": win_start[i].item(), "windowEnd": win_end[i].item(),
        "windowUnitIdx": unit_idx[i].item(),
    } for i in range(n)]


def score_predictions(gold: list[dict], pred: list[dict]) -> dict:
    """Aggregate metrics over a list of (gold, pred) example dicts from `decode_batch`."""
    n = len(gold)
    behaviour_correct = sum(g["behaviourIdx"] == p["behaviourIdx"] for g, p in zip(gold, pred))

    tp = fp = fn = 0
    for g, p in zip(gold, pred):
        for gv, pv in zip(g["fields"], p["fields"]):
            tp += gv and pv; fp += (not gv) and pv; fn += gv and (not pv)
    field_p = tp / (tp + fp) if tp + fp else 1.0
    field_r = tp / (tp + fn) if tp + fn else 1.0
    field_f1 = 2 * field_p * field_r / (field_p + field_r) if field_p + field_r else 0.0

    thr_total = thr_correct = 0
    for g, p in zip(gold, pred):
        if g["hasThreshold"]:
            thr_total += 1
            thr_correct += (p["hasThreshold"] and g["thresholdStart"] == p["thresholdStart"]
                            and g["thresholdEnd"] == p["thresholdEnd"] and g["thresholdIsMoreThan"] == p["thresholdIsMoreThan"])
    win_total = win_correct = 0
    for g, p in zip(gold, pred):
        if g["hasWindow"]:
            win_total += 1
            win_correct += (p["hasWindow"] and g["windowStart"] == p["windowStart"] and g["windowEnd"] == p["windowEnd"]
                            and g["windowUnitIdx"] == p["windowUnitIdx"])

    behaviour_acc = behaviour_correct / n if n else 0.0
    thr_exact = thr_correct / thr_total if thr_total else None
    win_exact = win_correct / win_total if win_total else None
    # Combined model-selection score: only average the metrics that have a defined denominator in
    # this split, so a dev slice with e.g. zero threshold-bearing examples can't silently zero it out.
    parts = [behaviour_acc, field_f1] + [x for x in (thr_exact, win_exact) if x is not None]
    return {"n": n, "behaviourAccuracy": round(behaviour_acc, 4), "fieldF1": round(field_f1, 4),
            "fieldPrecision": round(field_p, 4), "fieldRecall": round(field_r, 4),
            "thresholdExact": round(thr_exact, 4) if thr_exact is not None else None, "thresholdTotal": thr_total,
            "windowExact": round(win_exact, 4) if win_exact is not None else None, "windowTotal": win_total,
            "combined": round(sum(parts) / len(parts), 4)}
