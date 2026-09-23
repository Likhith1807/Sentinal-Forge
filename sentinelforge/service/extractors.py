"""Extractor backends behind one interface.

An extractor *proposes*; `sentinelforge.reconcile` decides. Every backend therefore returns either a
proposal dict (behaviourId / threshold / timeWindow / provenance ...) or None (no model: evidence-only), plus
metadata saying what ran, so an analysis record always states which extractor produced which claim.

Availability is probed lazily and reported, never assumed: the dashboard image ships without torch and the
fine-tuned checkpoint, and says so instead of failing at request time.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NLP_SRC = REPO_ROOT / "nlp" / "src"


@dataclass
class ExtractorInfo:
    id: str
    label: str
    description: str
    available: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _nlp_on_path() -> None:
    if str(NLP_SRC) not in sys.path:
        sys.path.insert(0, str(NLP_SRC))


def _finetuned_status() -> tuple[bool, str]:
    model_dir = Path(os.environ.get("SF_MODEL_DIR", REPO_ROOT / "nlp" / "models" / "roberta-base"))
    if not (model_dir / "model.pt").exists():
        return False, f"no checkpoint at {model_dir} (train with nlp/src/train_transformer.py or set SF_MODEL_DIR)"
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as exc:
        return False, f"{exc.name} is not installed (see requirements-train.txt)"
    return True, ""


def _prompted_status() -> tuple[bool, str]:
    if not os.environ.get("GROQ_API_KEY"):
        return False, "GROQ_API_KEY is not set"
    try:
        import groq  # noqa: F401
    except ImportError:
        return False, "the groq package is not installed"
    return True, ""


def list_extractors() -> list[ExtractorInfo]:
    ft_ok, ft_why = _finetuned_status()
    pr_ok, pr_why = _prompted_status()
    return [
        ExtractorInfo("finetuned", "Fine-tuned model + evidence check",
                      "RoBERTa multi-task extractor proposes; every value must be backed by the passage.", ft_ok, ft_why),
        ExtractorInfo("evidence", "Evidence check only (no model)",
                      "Deterministic condition finder alone; nothing is guessed, unreadable reports go to review.", True),
        ExtractorInfo("prompted", "Prompted LLM + evidence check",
                      "Sends the report text to an external LLM (Groq); off unless configured.", pr_ok, pr_why),
        ExtractorInfo("classical", "Regex baseline + evidence check",
                      "The classical baseline extractor, kept for comparison.", True),
    ]


def default_extractor() -> str:
    return "finetuned" if _finetuned_status()[0] else "evidence"


def extract(extractor: str, text: str) -> tuple[dict | None, dict]:
    """-> (proposal | None, metadata)."""
    t0 = time.time()
    meta = {"extractor": extractor}
    if extractor == "evidence":
        return None, {**meta, "seconds": 0.0, "note": "no model ran; conditions come from the passage alone"}
    _nlp_on_path()
    if extractor == "classical":
        import classical_extractor
        r = classical_extractor.extract(text)
        prop = {"behaviourId": r.behaviourId, "requiredFields": r.requiredFields, "policyFields": r.policyFields,
                "threshold": r.threshold, "timeWindow": r.timeWindow, "provenance": r.provenance}
    elif extractor == "finetuned":
        ok, why = _finetuned_status()
        if not ok:
            raise ExtractorUnavailable(why)
        import finetuned_extractor
        r = finetuned_extractor.extract(text)
        prop = {"behaviourId": r.behaviourId, "requiredFields": r.requiredFields, "policyFields": r.policyFields,
                "threshold": r.threshold, "timeWindow": r.timeWindow, "provenance": r.provenance}
        meta["modelSignal"] = {"behaviourSoftmax": round(float(r.raw.get("behaviourConfidence", 0.0)), 4),
                               "note": "uncalibrated softmax probability of the predicted class; never used to authorise compilation"}
    elif extractor == "prompted":
        ok, why = _prompted_status()
        if not ok:
            raise ExtractorUnavailable(why)
        import injection_guard
        import transformer_extractor
        guard = injection_guard.scan(text)
        if guard.flagged:
            raise ExtractorRefused("Prompt-injection guard flagged this report before any LLM call was made: " + "; ".join(guard.matches[:3]))
        spec = transformer_extractor.extract(text)
        prop = {"behaviourId": spec.get("behaviourId"), "requiredFields": spec.get("requiredFields", []),
                "policyFields": spec.get("policyFields", []), "threshold": spec.get("threshold"),
                "timeWindow": spec.get("timeWindow"), "provenance": spec.get("provenance", {})}
        meta["model"] = getattr(transformer_extractor, "DEFAULT_MODEL", "unknown")
    else:
        raise ValueError(f"unknown extractor {extractor!r}")
    meta["seconds"] = round(time.time() - t0, 3)
    return prop, meta


class ExtractorUnavailable(RuntimeError):
    pass


class ExtractorRefused(RuntimeError):
    pass
