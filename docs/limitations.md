# Limitations

What SENTINEL Forge does not do, does not know, and has not been shown to do. A project that lists only its wins is a brochure.

## What the system is

A compiler from a *narrow, closed* set of behaviours to Spark. It supports five authentication-log behaviours built from three
recipes (`sequence-then-trigger`, `distinct-count-within-window`, `policy-compare`). Everything else is **refused with a reason**.
It is not a general threat-report reader and it does not claim to be one.

## Language understanding

* **Recall is modest on unfamiliar wording.** On the frozen v2 holdout the default extractor compiled 19 of 30 supported reports
  correctly (63%); on v1 it was 44%. The remaining reports are *refused or sent to review*, never compiled wrongly (0 silent errors on
  both holdouts). Refusal is the designed failure — but it is still a failure to help.
* **Precision on must-refuse reports is not perfect.** Before the post-v2 fixes the default path accepted 5 of 41 reports that should
  have been refused (12%); v1: 4 of 71 (6%). After a fix a holdout is spent, so the fixed behaviour has regression tests, not a
  held-out number.
* **Both holdouts are small** (41 and 30 supported reports) and labelled by one person plus, for v1 only, a blind LLM reviewer.
  Confidence intervals are wide, and the UI shows them.
* **Only English prose about authentication logs** is handled. The evidence finder is rule-based; it does not learn.
* **The fine-tuned model is not the default** (see `docs/evaluation.md` §2); it lowered recall when combined with the evidence check.
* **The prompted-LLM comparison is incomplete** (provider quota). The partial rows are labelled and are not quoted as a comparison.
* **Quote verification is a substring check**, reported as such; it does not prove a quote supports a value.

## Detection semantics

Defined in `docs/spec/detection-semantics.md` (v2). The choices worth knowing:

* Time is microsecond RFC 3339 UTC; a window is the **closed** interval `[t − W, t]`.
* Counts have explicit meaning per behaviour (event count, distinct accounts, distinct hosts). A report that says "logins" without
  saying which is sent to review — it is never guessed.
* Malformed timestamps are **quarantined**, not dropped silently and not coerced.
* Duplicates are removed by `event_id`. Batch keeps the earliest copy; streaming keeps the first-*delivered* copy. They are identical for an exact
  redelivery and can differ for *conflicting* duplicates (same id, different content) - a known, documented divergence (`detection-semantics.md` §5).
  Duplicate policy rows that disagree make the account `policy_conflict` rather than multiplying the join.
* Alerts are **rising-edge**: one alert when a key crosses the threshold, not one per additional event.
* Not supported: negative or fractional thresholds, ranges ("between 3 and 5"), equality ("exactly 5"), rates ("5 per hour"),
  averages, conditions on fields the schema does not have (IP reputation, geography, browser, subnet), qualifiers
  ("only on weekends", "except the scanner allow-list"), and any behaviour outside the five.
* Sigma output exists only for reports whose meaning Sigma can carry without loss (`sentinelforge/sigma.py`); anything else is
  refused rather than exported approximately.

## Streaming

* Order-dependent results need a **lateness policy**. Events later than it are *reported* as `late`, never silently dropped; agreement
  with batch is defined against the input minus those events.
* The output is **effectively-once under conditions**, not unconditionally exactly-once: replayable immutable source files, a
  deterministic state function, a single writer per checkpoint, and the checkpoint and the output directory surviving together.
  Lose the checkpoint but keep the output and alerts are re-emitted.
* A redelivery older than the retained window cannot be recognised as a duplicate; it is counted by Spark's watermark, not identified.
* A **hot key** holds state proportional to the events inside its window and runs on one task. `docs/benchmarks.md` measures where that starts to
  hurt on this machine; it does not remove it.
* The last `lateness` of each key's tail is held back until a later event or a heartbeat advances the watermark.

## Scale and performance

* **Everything was measured on one laptop** (`docs/benchmarks.md` has the hardware), one JVM, `local[*]`. There is **no distributed
  measurement** and no claim of one. Nothing here shows the design scales across machines.
* The data is **synthetic**. The compiler is exactly correct on 36.7M generated events; that says the engine is correct and how fast it is, not that
  it would find a real attacker.
* Small sample sizes limit the statistics. Percentiles are reported only where the sample supports them.

## The product

* **Local-demo mode is loopback-only** and has no login. A shared deployment needs the token/role mode (`docs/deployment.md`),
  which has been tested but not penetration-tested.
* State is SQLite (WAL) on one machine. Concurrent approvals are safe (compare-and-set), but this is not a multi-node service.
* The dashboard's *demo* and *archived* data are labelled as such; nothing on the Evaluation screen is computed live.
* No user study. **No practising detection engineer has reviewed this**, and no claim about usefulness in a real SOC is made.

## Reproduction

The container and CI gates were built and run by the author. An independent reproduction on a machine the author does not control
has not been done.
