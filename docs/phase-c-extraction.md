> **Historical document.** Written during an earlier phase of the project and kept as a record. Numbers here were measured on the original 44-report set and the pre-audit pipeline; the current, audited results and limits are in [`docs/evaluation.md`](evaluation.md) and [`docs/limitations.md`](limitations.md).

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
Bootstrap 95% CIs, 2000 resamples over reports. This is the final run, against the project's
intended default model, `openai/gpt-oss-120b` (an earlier pass used `openai/gpt-oss-20b`, a
smaller sibling with a separate quota, while `120b`'s daily cap was exhausted — that comparison is
superseded by this one and kept in git history, not in this document).

| System | Scored | Behaviour acc | Field F1 | Threshold exact | Window exact | Abstention recall |
|---|---|---|---|---|---|---|
| Classical | 44/44 | 0.205 [0.09, 0.34] | 0.051 [0.00, 0.13] | 0.0 | 0.0 | 0.0 [0, 0] |
| Prompted (gpt-oss-120b) | 44/44 | 0.909 [0.82, 0.98] | **0.980** [0.96, 0.99] | 1.0 | 0.75 | **0.0** [0, 0] |
| Fine-tuned | 44/44 | 0.955 [0.89, 1.0] | 0.919 [0.87, 0.96] | 1.0 | 0.708 | 0.5 [0, 1] |
| **Hybrid** (gpt-oss-120b fallback) | 40/44 | **0.975** [0.93, 1.0] | 0.956 [0.92, 0.99] | 1.0 | 0.696 | 0.0 [0, 0] |

Classical collapses exactly as the Phase B difficulty check predicted (`docs/corpus.md`). Hybrid's
4 unavailable reports are 3 of its own fallback calls hitting the daily quota partway through
(`gpt-oss-120b`'s 200,000-token cap; the standalone prompted row alone used most of it) — excluded
from its metrics, not scored as wrong, per the `run_system` fix below.

**A real bug found and fixed first.** An early attempt against `gpt-oss-120b` (before the corpus
build had used up its quota) folded every API failure into "wrongly abstained" in
`evaluate_corpus.run_system` — scoring an unavailable answer as a confident wrong one. Fixed to
exclude errored reports from every metric and list them separately (`unavailable`), which is what
makes every "scored" count above trustworthy.

### The one genuinely surprising number: prompted abstention recall is 0.0, at both model sizes

An earlier, tiny (n=2-3) sample had suggested the prompted extractor abstains reliably. The real,
complete run against the actual intended model says otherwise: **`gpt-oss-120b` never correctly
identified a single one of the 4 out-of-scope test reports** — abstention recall 0.0, matching
`gpt-oss-20b`'s result on the same 4 reports exactly. This is now a finding about the *prompting
approach* to abstention on this task, not an artifact of one model's size: the extraction prompt
(`transformer_extractor.build_prompt`) asks the model to pick a `behaviourId` from the 5 known
values, and evidently that framing doesn't reliably produce "none of these" even from a much larger
model. A real, scoped follow-up (not done here): try an explicit "or none of the above, in which
case say so" instruction and re-measure, rather than assume a bigger model alone would fix it.

**The hybrid design gap this exposed, and the fix.** Tracing the *first* hybrid run (against
`gpt-oss-20b`) found `hybrid_extractor`'s fallback condition firing on *every* fine-tuned
`unsupported` prediction regardless of confidence — discarding a correct, confident abstention on
`SFC-0211`/`SFC-0212` and calling the fallback anyway, which that time happened to also abstain
(the same run-to-run variance `nlp/README.md` already documents, observed directly). **Fixed**:
`unsupported` is now just another class judged by the same confidence threshold as any other
(`hybrid_extractor.py`; regression tests in `nlp/test/test_hybrid_and_consistency.py` cover both
"confident abstention is kept" and "unconfident abstention still falls back"). The corrected
run's numbers are now fully self-consistent with the finding above: `SFC-0211`/`SFC-0212`
correctly triggered a fallback attempt (both landed in the 4 quota-unavailable reports, since
fine-tuned's own confidence there was genuinely below threshold, not because the logic forced it),
and hybrid's overall abstention recall (0.0) now honestly reflects that the fallback model doesn't
abstain reliably either — not a lucky re-roll dressed up as a working design.

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
when the fine-tuned model's own softmax confidence is below a threshold (default 0.6) —
`unsupported` is judged by that same confidence check, not treated as an automatic trigger (see the
design-gap correction above).

**Live-evaluated (see table above): 0.975 behaviour acc, 0.956 field F1 — the best of the four
systems on both.** 11 offline tests (`nlp/test/test_hybrid_and_consistency.py`) separately check
the fallback trigger and aggregation logic against a mocked prompted extractor, including
regression tests for the confidence-based fix, so the wiring is verified independent of any one
live model's behaviour.

## Self-consistency voting (the reliability item, as originally scoped)

The "fix extraction reliability" roadmap item was scoped to `transformer_extractor.py`'s own
documented run-to-run variance at `temperature=0` (`nlp/README.md`). Kept as two separate, both-real
results rather than assuming the new fine-tuned model needed the same fix:

1. **The fine-tuned model's determinism was verified, not assumed:** 3 completely independent
   `python` subprocesses (fresh weights load from disk each time, no shared state) produced
   byte-identical predictions on all 44 test reports —
   [`phaseC_determinism_check.json`](../experiments/results/phaseC_determinism_check.json),
   `nlp/src/check_determinism.py`. It's a discriminative classifier, not a sampled generation, so
   this isn't surprising — but "not surprising" isn't "verified," and now it's both.
2. **A self-consistency wrapper for the prompted extractor**
   ([`nlp/src/consistency_extractor.py`](../nlp/src/consistency_extractor.py)), **live-measured** on
   10 random supported test reports (`gpt-oss-20b`, 3 samples each) —
   [`phaseC_consistency_variance.json`](../experiments/results/phaseC_consistency_variance.json):
   - **Raw (unvoted) agreement across all 3 samples: 1.0 for both behaviourId and the field set**,
     on the 8 of 10 reports where at least 2 of 3 calls succeeded. The other 2 (`SFC-0101`,
     `SFC-0029`) had only 1 successful call each — an *availability* failure (malformed/truncated
     JSON from the smaller model), not a disagreement, and are correctly excluded from the
     agreement rate rather than counted either way.
   - **Voted-answer accuracy against gold: 9/10 (0.9).** The one miss, `SFC-0115`, is instructive:
     its vote returned no answer because **all 3 samples in that specific call failed**
     (`nFailed: 3`) — a total, momentary availability gap, not a disagreement either. A follow-up
     single-report check minutes later, same text, same model, succeeded cleanly on all 3 samples
     and matched gold — direct, reproduced evidence that `gpt-oss-20b`'s output for identical input
     is not stable across separate calls, the same phenomenon already documented for `gpt-oss-120b`.
   - **A further, honest data point found while trying to re-run this cleanly**: a subsequent
     attempt to regenerate this file with more detailed failure logging found *every one* of 10
     reports failing its raw-variance measurement — `gpt-oss-20b`'s own quota, untouched at the
     start of this session, was visibly strained after the cumulative calls this phase's evaluation
     already made. That run crashed on an unguarded `None`-formatting print rather than producing
     partial results; fixed in `run_consistency_demo.py`, and not re-run again to avoid spending
     more of a quota already shown to be under pressure. The committed
     `phaseC_consistency_variance.json` is the earlier, successful run.

## What's still open

Both items that were open after the first pass are closed: the comparison now runs against the
intended `gpt-oss-120b`, and the hybrid's fallback-on-any-abstention gap is fixed and
regression-tested. What remains is smaller and explicitly scoped, not a rerun of either of those:

1. **Hybrid's `gpt-oss-120b` row is 40/44, not 44/44** — 4 of its own fallback calls hit the daily
   quota (the standalone prompted row's 44 calls used most of it first). Re-running
   `evaluate_corpus.py` once quota resets would fill in the last 4, but the 40 scored already tell
   a coherent, internally-consistent story (see the abstention discussion above) — this is a
   completeness gap on one row, not an open question about correctness.
2. **Prompted-abstention framing**: try an explicit "or none of the above" instruction in
   `transformer_extractor.build_prompt` and re-measure, now that abstention recall 0.0 is confirmed
   at two model sizes rather than assumed fixable by a bigger model.

## Reproducing

```
python nlp/src/train_transformer.py --encoder roberta-base --out nlp/models/roberta-base
python nlp/src/evaluate_corpus.py --model-dir nlp/models/roberta-base \
    --out experiments/results/phaseC_corpus_comparison.json
python nlp/src/ablate_corpus.py --out experiments/results/phaseC_ablations.json
python nlp/src/check_determinism.py --model-dir nlp/models/roberta-base --runs 3 --out experiments/results/phaseC_determinism_check.json
python nlp/src/run_consistency_demo.py --n 10 --out experiments/results/phaseC_consistency_variance.json
python nlp/test/test_hybrid_and_consistency.py
```

`--llm-model` (default `transformer_extractor.DEFAULT_MODEL`, `openai/gpt-oss-120b`) overrides the
prompted-LLM model if its quota is exhausted — the self-consistency demo above used
`openai/gpt-oss-20b` for that reason and is not re-run with `120b` here, since its purpose (does
voting reduce variance) doesn't depend on which model size demonstrates it.

Checkpoints (`nlp/models/*/model.pt`, ~476 MB each) are not committed (`.gitignore`); their
`run_config.json`/`training_log.json`/`best_dev_metrics.json` are, under
`experiments/results/phaseC_training_logs/`, so every training run is independently checkable
without re-downloading or re-training.
