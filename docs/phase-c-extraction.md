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

**A model substitution, stated up front.** Groq's daily token quota (200,000) for the project's
default model, `openai/gpt-oss-120b`, was already at 198,715 from the corpus build's LLM-rewrite
tier. Two full-evaluation attempts against it scored 3/44 and 2/44 before hitting the cap each
time — not a result. `openai/gpt-oss-20b` (same vendor family, smaller, a separate quota bucket)
was available, so the comparison below uses it for every prompted-LLM and hybrid-fallback call,
**labelled as such everywhere it appears**. This is not the same comparison Phase 2 ran — it is a
real, complete one against a smaller sibling model, not a partial one against the intended model.
A `gpt-oss-120b` re-run is still open once its quota resets (see "What's still open").

| System | Scored | Behaviour acc | Field F1 | Threshold exact | Window exact | Abstention recall |
|---|---|---|---|---|---|---|
| Classical | 44/44 | 0.205 [0.09, 0.34] | 0.051 [0.00, 0.13] | 0.0 | 0.0 | 0.0 |
| Prompted (gpt-oss-20b) | 43/44 | 0.907 [0.81, 0.98] | **0.993** [0.98, 1.0] | 1.0 | 0.75 | **0.0** [0, 0] |
| Fine-tuned | 44/44 | 0.955 [0.89, 1.0] | 0.919 [0.87, 0.96] | 1.0 | 0.708 | 0.5 [0, 1] |
| **Hybrid** (gpt-oss-20b fallback) | 44/44 | **0.977** [0.93, 1.0] | 0.957 [0.92, 0.99] | 1.0 | 0.708 | 0.75 [0, 1] |

Classical collapses exactly as the Phase B difficulty check predicted (`docs/corpus.md`). One
prompted call failed outright (`SFC-0174`: "Model output was not parseable JSON: ''") — excluded
from that row's metrics, not scored as wrong, per the `run_system` fix below.

**A real bug found and fixed first.** The first attempt against `gpt-oss-120b` folded every API
failure into "wrongly abstained" in `evaluate_corpus.run_system` — scoring an unavailable answer
as a confident wrong one. Fixed to exclude errored reports from every metric and list them
separately (`unavailable`), which is what makes the 43/44 and 44/44 counts above trustworthy.

### The one genuinely surprising number: prompted abstention recall is 0.0, not 1.0

An earlier, tiny (n=2-3) sample against `gpt-oss-120b` had suggested the prompted extractor
abstains reliably. The real, complete `gpt-oss-20b` run says the opposite for *this* model: **it
never correctly identified a report as out-of-scope** — abstention recall 0.0, CI [0, 0] (the CI is
degenerate because bootstrap-resampling 4 identical failures always resamples 4 failures). Read as
a smaller-model finding, not a retraction of anything about `gpt-oss-120b`, which hasn't had a real
sample yet either way.

**Hybrid's 0.75 abstention recall needs the same scrutiny, not a victory lap.** With only 4
unsupported test reports, its CI is the maximally uninformative [0, 1] — this number is not
reliable evidence of anything on its own. Tracing it per report against the committed JSON found
something more interesting than the aggregate: on `SFC-0211` and `SFC-0212`, the fine-tuned model
already abstained correctly, but the hybrid's `needs_fallback` condition (`primary.behaviourId is
None or confidence < threshold`) fires on *any* fine-tuned abstention — so it called the prompted
model anyway, discarding a correct answer, and the prompted model's *fresh, independent* call
happened to also abstain this time, even though the same model's separately-sampled call on the
same two reports in the standalone `transformer-prompted` row did not. That is the documented
`gpt-oss-120b` run-to-run variance (`nlp/README.md`) now directly observed in `gpt-oss-20b` too, on
the same day, same input, different call. The hybrid's headline number is therefore **partly a
favourable re-roll**, not a demonstrated property of the fallback design — a real design gap worth
naming: falling back on *every* fine-tuned abstention, rather than only on low-confidence
non-abstentions, discards a correct answer, and should be reconsidered before this hybrid design
is treated as final. Filed as an open item, not silently fixed.

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

**Live-evaluated (see table above): 0.977 behaviour acc, 0.957 field F1 — the best of the four
systems on both, with the abstention-recall caveat above.** 7 offline tests
(`nlp/test/test_hybrid_and_consistency.py`) separately check the fallback trigger and aggregation
logic against a mocked prompted extractor, so the wiring is verified independent of any one live
model's behaviour. **Known design gap, found by tracing the live run, not assumed up front:**
falling back on every fine-tuned `unsupported` prediction discards a correct answer whenever the
fallback call disagrees — worth changing to skip the fallback when fine-tuned already abstained
(not just when its confidence is low), before relying on this hybrid design further.

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

1. **A `gpt-oss-120b` run of `evaluate_corpus.py`**, once its daily quota resets, for the
   originally-intended model (today's numbers, for the prompted and hybrid rows, are `gpt-oss-20b`).
2. **The hybrid fallback-on-abstention design gap** named above.
3. Both are now precisely scoped follow-ups, not blanket "re-run when quota allows" placeholders —
   the underlying mechanisms (evaluation harness, hybrid, self-consistency) are built, tested, and
   have each produced at least one real, complete result today.

## Reproducing

```
python nlp/src/train_transformer.py --encoder roberta-base --out nlp/models/roberta-base
python nlp/src/evaluate_corpus.py --model-dir nlp/models/roberta-base --llm-model openai/gpt-oss-20b \
    --out experiments/results/phaseC_corpus_comparison.json
python nlp/src/ablate_corpus.py --out experiments/results/phaseC_ablations.json
python nlp/src/check_determinism.py --model-dir nlp/models/roberta-base --runs 3 --out experiments/results/phaseC_determinism_check.json
python nlp/src/run_consistency_demo.py --n 10 --model openai/gpt-oss-20b --out experiments/results/phaseC_consistency_variance.json
python nlp/test/test_hybrid_and_consistency.py
```

`--llm-model` defaults to `transformer_extractor.DEFAULT_MODEL` (`openai/gpt-oss-120b`); pass an
override if its quota is exhausted, as it was for every live run in this document.

Checkpoints (`nlp/models/*/model.pt`, ~476 MB each) are not committed (`.gitignore`); their
`run_config.json`/`training_log.json`/`best_dev_metrics.json` are, under
`experiments/results/phaseC_training_logs/`, so every training run is independently checkable
without re-downloading or re-training.
