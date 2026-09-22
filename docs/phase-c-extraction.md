# Phase C — A Fine-Tuned Extractor (v1)

Phase 2 (`nlp/README.md`) compared two *untrained* extractors — regex/keyword rules and a
prompted pretrained LLM — on 5 held-out reports, explicitly not claiming a trained model because
10 training reports isn't enough to generalize from. The v2 corpus (`docs/corpus.md`, 201 reports,
118 in train) changes that. This phase fine-tunes a real transformer on it and compares four
systems, honestly, including where the comparison is currently incomplete and why.

## Annotation schema

Every label is read directly off the corpus's own gold (`data/corpus/gold/*.gold.json`) — nothing
here was separately hand-annotated. The full schema, and exactly how each label is derived, is
documented as code in [`nlp/src/corpus_dataset.py`](../nlp/src/corpus_dataset.py)'s module
docstring: a 6-way behaviour label (5 behaviours + `unsupported`), an 11-way multi-label field
set, and — only for the 3 behaviours that have one — a threshold value/span/"more than"-phrasing
flag and a window amount/span/unit. Character spans are converted to token ranges via the
tokenizer's offset mapping; any span that falls outside `max_length` after truncation is dropped
from supervision and counted, never silently kept.

## Model

One shared encoder (`AutoModel`, RoBERTa-family) with five heads — behaviour classification,
field multi-label classification, threshold span extraction, a "more than" binary flag, window
span extraction, and window-unit classification — trained jointly
([`nlp/src/multitask_model.py`](../nlp/src/multitask_model.py)). `nlp/src/train_transformer.py`
fits on the corpus **train** split only and selects the checkpoint on **dev** only; the **test**
split is read exactly once, by `evaluate_corpus.py`, after the checkpoint is frozen — no tuning
decision is ever made by looking at test performance.

Trained on an RTX 4060 Laptop GPU (8 GB). The `roberta-base` run converged in **119.5 s** (best
epoch 9 of 17, early-stopped after 8 epochs with no dev improvement); dev combined score 0.910.

## Main comparison (test split, n=44)

`python nlp/src/evaluate_corpus.py` — [`phaseC_corpus_comparison.json`](../experiments/results/phaseC_corpus_comparison.json).
Bootstrap 95% CIs, 2000 resamples over reports.

| System | Scored | Behaviour acc | Field F1 | Threshold exact | Window exact | Abstention recall |
|---|---|---|---|---|---|---|
| Classical | 44/44 | 0.205 [0.09, 0.34] | 0.051 [0.00, 0.13] | 0.0 | 0.0 | 0.0 |
| Transformer-prompted | **2/44** | n/a | n/a | n/a | n/a | n/a |
| **Fine-tuned** | 44/44 | **0.955** [0.89, 1.0] | 0.919 [0.87, 0.96] | 1.0 | 0.708 | 0.5 |

Classical collapses exactly as the Phase B difficulty check predicted (`docs/corpus.md`) — it only
reads the old "Analyst-confirmed detection parameters" heading, which most v2 reports don't have.

**The prompted-LLM row is not a real comparison point.** Groq's daily token quota (200,000) was
already at 198,715 from the corpus build's LLM-rewrite tier. The first run of the full 44-report
evaluation today hit that wall almost immediately, with every failure folded into "wrongly
abstained" — a bug in `evaluate_corpus.run_system`, fixed on the spot to **exclude** errored
reports from every metric instead, since an API failure is unavailable data, not a scored wrong
answer. Two re-runs after that fix scored 3/44 and then 2/44 before hitting the cap again each
time. The handful of calls that did go through all succeeded, which is *some* signal the prompted
extractor still works, but n=2 is not a result. **Re-run `evaluate_corpus.py` once the quota
resets for a real number** (the committed
[`phaseC_corpus_comparison.json`](../experiments/results/phaseC_corpus_comparison.json) is the
2/44 run).

## Ablation 1 — encoder choice: generic vs domain-adapted

`ehsanaghaei/SecureBERT` (cybersecurity-domain RoBERTa) vs generic `roberta-base`, identical heads,
identical training procedure, evaluated on the same test split
([`phaseC_ablations.json`](../experiments/results/phaseC_ablations.json)):

| Encoder | Behaviour acc | Field F1 | Best epoch |
|---|---|---|---|
| roberta-base | **0.955** | 0.919 | 9 |
| SecureBERT | 0.886 | **0.937** | 15 |

**No clear winner, and that's the honest finding.** SecureBERT edges out field F1; roberta-base
wins behaviour accuracy by a larger margin, converges faster (epoch 9 vs 15), and starts from a
lower loss (12.37 vs 14.08 at epoch 1). Domain-adapted pretraining did not clearly help on this
corpus — plausibly because the corpus's language is closer to general incident-report prose than
to SecureBERT's pretraining corpus, and because 118 training examples isn't enough to show a
transfer advantage either way. Not the result a "domain-specific must be better" narrative would
predict, reported as such rather than reached for a cleaner story (the same discipline
`nlp/README.md` already applied to Phase 2's classical-vs-transformer result).

## Ablation 2 — does the guarded LLM-rewrite training tier actually help?

`docs/corpus.md` built a second, LLM-rewritten tier under guards specifically to test
generalization beyond template phrasing. This ablation is the direct test of whether that design
choice paid off: train on template-tier reports only vs template + LLM-rewrite, evaluate each
model separately against the test split's template-tier and LLM-rewrite-tier reports.

| Trained on | Evaluated on | Behaviour acc | Field F1 |
|---|---|---|---|
| template + LLM-rewrite | template (n=22) | 0.955 | 0.915 |
| template + LLM-rewrite | LLM-rewrite (n=22) | **0.955** | 0.923 |
| template only | template (n=22) | 0.909 | **0.962** |
| template only | LLM-rewrite (n=22) | **0.773** | 0.915 |

**A real, clean, and useful result.** A model trained only on the template tier drops 18 points of
behaviour accuracy (0.955 -> 0.773) the moment it sees LLM-rewritten phrasing it never trained on
— a template-tier-only extractor generalizes noticeably worse to reworded reports. Training on
both tiers together closes that entire gap (0.955 on both) at essentially no cost on template-tier
performance. This is measured evidence that the LLM-rewrite tier's guards (docs/corpus.md: every
gold-bearing phrase preserved verbatim, no invented numbers or identifiers) produced training data
that generalizes, not just more data — the specific thing the corpus was built to test.

## Hybrid extractor (roadmap's 4th system)

[`nlp/src/hybrid_extractor.py`](../nlp/src/hybrid_extractor.py) is motivated by the one concrete
tradeoff the main comparison surfaced: the fine-tuned model's `abstentionRecall` (correctly
flagging an out-of-scope report) is 0.5 — it under-predicts the `unsupported` class, its smallest
training class (9 of 118 train examples) — while never wrongly abstaining on a real behaviour
(`falseAbstentionRate` 0.0). Rather than an arbitrary ensemble vote (the classical extractor is far
too weak on this corpus to usefully vote on anything, per the ablation above), the hybrid uses the
fine-tuned model for every report and calls the prompted extractor as a second opinion **only**
when the fine-tuned model predicts `unsupported` or its own softmax confidence is below a
threshold (default 0.6) — the one condition the data says is worth an API call.

**Wiring verified, live evaluation not yet run.** 7 offline tests
(`nlp/test/test_hybrid_and_consistency.py`) check the fallback trigger and aggregation logic
against a mocked prompted extractor — no API, no model checkpoint. A real accuracy number for the
hybrid needs the same Groq quota the main comparison is waiting on; `--out` in
`evaluate_corpus.py` already writes a `hybrid` row once that's run.

## Self-consistency voting (the reliability item, as originally scoped)

The "fix extraction reliability" roadmap item was scoped to `transformer_extractor.py`'s own
documented run-to-run variance at `temperature=0` (`nlp/README.md`). Rather than assume the new
fine-tuned model needed the same fix, that's kept as two separate, both-real results:

1. **The fine-tuned model's determinism was verified, not assumed:** 3 completely independent
   `python` subprocesses (fresh weights load from disk each time, no shared state) produced
   byte-identical predictions on all 44 test reports —
   [`phaseC_determinism_check.json`](../experiments/results/phaseC_determinism_check.json),
   `nlp/src/check_determinism.py`. It's a discriminative classifier, not a sampled generation, so
   this isn't surprising — but "not surprising" isn't "verified," and now it's both.
2. **A self-consistency wrapper for the prompted extractor**
   ([`nlp/src/consistency_extractor.py`](../nlp/src/consistency_extractor.py)): calls it
   `n_samples` times and takes the majority answer per field (behaviourId by mode, each field kept
   only if it appears in a strict majority of calls, threshold/window by majority value). 2 offline
   tests confirm the voting logic itself (including the "no majority" case, which correctly
   returns `None` rather than guessing). `measure_variance()` is written to quantify the actual
   before/after agreement rate on a real sample — **not yet run**, same quota constraint as above.

## What's still open (all blocked on the same external constraint)

Everything below needs the Groq daily token quota to reset, not further engineering:

1. Re-run `evaluate_corpus.py` for a real transformer-prompted number (currently n=2/44).
2. Re-run `evaluate_corpus.py` with the hybrid system included, now that it's wired in.
3. Run `consistency_extractor.measure_variance()` on a real sample to quantify the actual
   before/after agreement-rate improvement from self-consistency voting.

None of these are a "the model doesn't work" gap — they're an availability gap in one comparison
input, documented as such rather than worked around with a smaller, quieter claim.

## Reproducing

```
python nlp/src/train_transformer.py --encoder roberta-base --out nlp/models/roberta-base
python nlp/src/evaluate_corpus.py --model-dir nlp/models/roberta-base --out experiments/results/phaseC_corpus_comparison.json
python nlp/src/ablate_corpus.py --out experiments/results/phaseC_ablations.json
python nlp/src/check_determinism.py --model-dir nlp/models/roberta-base --runs 3 --out experiments/results/phaseC_determinism_check.json
python nlp/test/test_hybrid_and_consistency.py
```

Checkpoints (`nlp/models/*/model.pt`, ~476 MB each) are not committed (`.gitignore`); their
`run_config.json`/`training_log.json`/`best_dev_metrics.json` are, under
`experiments/results/phaseC_training_logs/`, so every training run is independently checkable
without re-downloading or re-training.
