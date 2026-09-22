"""Hybrid extractor: fine-tuned primary, prompted-LLM fallback — motivated by a measured tradeoff,
not an arbitrary combination.

`experiments/results/phaseC_corpus_comparison.json` found a specific, opposite-direction tradeoff
between the two learned systems on the corpus test split:

  - the fine-tuned model has the higher behaviour accuracy and field F1 overall, but its
    `abstentionRecall` (catching a genuinely out-of-scope report) was well below the prompted
    extractor's;
  - the prompted extractor abstains reliably, at the cost of an API call, latency, cost, and the
    documented run-to-run variance (`nlp/README.md`).

So the hybrid uses the fine-tuned model for every report (fast, deterministic, free), and only
calls the prompted extractor as a second opinion when the fine-tuned model's own top-class softmax
probability is below `confidence_threshold` — regardless of which class that is. That is the one
condition the measured tradeoff says is worth spending an API call on — not a general-purpose
ensemble vote, which the data doesn't support (the classical extractor is far too weak on this
corpus, per `docs/corpus.md`, to usefully vote on anything).

CORRECTION (found by tracing the first live run, not assumed up front): an earlier version fell
back on *every* fine-tuned `unsupported` prediction unconditionally, regardless of confidence —
discarding a correct, confident abstention whenever the fallback call's independently-sampled
answer happened to disagree with it (`docs/phase-c-extraction.md` traces `SFC-0211`/`SFC-0212`,
where this cost a correct answer and only "worked out" because the fallback call happened to also
abstain that time — a lucky re-roll, not a property of the design). `unsupported` is now just
another class whose own confidence is checked like any other; a confident abstention is kept, an
unconfident one still gets a second opinion.

This makes the hybrid only as deterministic as how often it falls back — reported per run via
`fallbackRate`, never hidden.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finetuned_extractor  # noqa: E402
import transformer_extractor  # noqa: E402

DEFAULT_CONFIDENCE_THRESHOLD = 0.6


@dataclass
class HybridExtractionResult:
    behaviourId: str | None
    requiredFields: list = field(default_factory=list)
    policyFields: list = field(default_factory=list)
    excludedFields: list = field(default_factory=list)
    threshold: dict | None = None
    timeWindow: dict | None = None
    provenance: dict = field(default_factory=dict)
    source: str = "fine-tuned"          # "fine-tuned" or "prompted-fallback"
    fineTunedConfidence: float = 0.0


def extract(report_text: str, model_dir=finetuned_extractor.DEFAULT_MODEL_DIR,
           confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
           llm_model: str = transformer_extractor.DEFAULT_MODEL) -> HybridExtractionResult:
    primary = finetuned_extractor.extract(report_text, model_dir)
    confidence = primary.raw.get("behaviourConfidence", 0.0)
    if confidence >= confidence_threshold:
        return HybridExtractionResult(
            behaviourId=primary.behaviourId, requiredFields=primary.requiredFields, policyFields=primary.policyFields,
            threshold=primary.threshold, timeWindow=primary.timeWindow, provenance=primary.provenance,
            source="fine-tuned", fineTunedConfidence=confidence)

    fallback = transformer_extractor.extract(report_text, model=llm_model)
    behaviour_id = fallback.get("behaviourId")
    if behaviour_id not in finetuned_extractor.BEHAVIOUR_LABELS[:-1]:   # "unrecognized:*" convention -> abstain
        behaviour_id = None
    return HybridExtractionResult(
        behaviourId=behaviour_id, requiredFields=fallback.get("requiredFields", []),
        policyFields=fallback.get("policyFields", []), threshold=fallback.get("threshold"),
        timeWindow=fallback.get("timeWindow"), provenance=fallback.get("provenance", {}),
        source="prompted-fallback", fineTunedConfidence=confidence)
