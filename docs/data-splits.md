# Report Train / Held-Out Split (v1)

> **Two corpora, two purposes.** This file describes the 15-report **v1 correctness harness** (Phase 1): hand-authored,
> single-template, kept because every earlier result is scored against it. It is **not** big enough to produce a
> meaningful F1. The larger, family-split, leakage-checked **v2 corpus** (`data/corpus/`, Phase B) is documented in
> [`corpus.md`](corpus.md); numbers reported for training or evaluation at scale come from v2 and must say so.

15 reports across the 5 behaviours in [`docs/behaviours.md`](behaviours.md):
3 per behaviour — the original, a **paraphrase** (same incident, reworded),
and a **held-out** report (a genuinely different incident of the same
behaviour class).

## The rule this split follows

> Keep report families and near-duplicates out of held-out test splits.
> — README.md, Evaluation plan

A paraphrase is, by construction, a near-duplicate of its original — same
account, same numbers, same incident, different wording. Putting the
paraphrase in held-out while the original is in train would leak lexical
overlap into the score and inflate extraction F1 without testing
generalisation at all. So **every paraphrase stays in train, next to its
original** — the held-out set is never a reworded copy of something the
model already saw, it's a different incident entirely (different account,
different host, and in several cases a deliberately different peripheral
detail chosen to catch a model that latched onto the wrong signal).

## Assignment

| Behaviour | Train (2 reports) | Held-out (1 report) | What the held-out report specifically tests |
|---|---|---|---|
| B1 — repeated failed login → success | SF-SAMPLE-001, SF-SAMPLE-006 | SF-SAMPLE-011 | `auth_method` on the success (`token`, not `password`) isn't part of the pattern — see the same bug the direct-LLM baseline actually made with `mfa_used` (`experiments/baselines/direct_llm/README.md`) |
| B2 — password spray | SF-SAMPLE-002, SF-SAMPLE-007 | SF-SAMPLE-012 | Rule generalises past the exact account count (5→6) and window shape (10min→~12min observed) without the threshold itself moving |
| B3 — concurrent sessions | SF-SAMPLE-003, SF-SAMPLE-008 | SF-SAMPLE-013 | A looser timing gap (9 min vs. the training examples' 4 min) still inside the same 15-minute window |
| B4 — service-account interactive auth | SF-SAMPLE-004, SF-SAMPLE-009 | SF-SAMPLE-014 | Mismatch direction reversed (certificate used where token expected, vs. training's password-where-certificate-expected) — catches a rule that hardcodes "flag password" |
| B5 — MFA bypass | SF-SAMPLE-005, SF-SAMPLE-010 | SF-SAMPLE-015 | `auth_method` varies (`token`, not `password`) while `mfa_used: false` is the only thing that should matter |

**Train:** 10 reports (SF-SAMPLE-001–010). **Held-out:** 5 reports
(SF-SAMPLE-011–015), one per behaviour.

## What this split is, and isn't, good for

Ten training reports is enough to validate that Phase 2's extractor and
Phase 3's checker run correctly end-to-end and to catch the specific
failure modes each held-out report targets. It is not enough data to train
a Transformer extractor from scratch or to produce a statistically
meaningful F1 number — that requires substantially more report volume per
behaviour, which is tracked as further Phase 1/2 work, not claimed as done
here. Treat this split as the correctness harness for the pipeline, and
the eventual larger corpus as what produces publishable numbers.
