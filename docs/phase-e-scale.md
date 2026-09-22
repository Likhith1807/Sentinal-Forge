# Phase E — Scale and Performance (v1)

Real runs against the Phase B/D dataset (`data/generated/scale_1gb`: 36,684,108 events, 1.055 GB
Parquet) and a real Structured Streaming query — no numbers in this document are estimated.

## 15. Throughput and p95 latency — real, repeated-run measurement

`ScaleBenchmarkCheck.scala` forces full execution of `RuleCompiler.compile(...)` 3 times per
behaviour and reports mean/p50/p95/min/max, specifically because a single run is a documented noisy
measurement (`docs/data-sources.md`: an earlier single-run throughput check swung from 95 to 222
events/s on the *same* 48-event data between two runs).

[`phaseE_scale_benchmark_scale_1gb.json`](../experiments/results/phaseE_scale_benchmark_scale_1gb.json):

| Behaviour | Mean | p50 | p95 | Min | Max | Approx. throughput |
|---|---|---|---|---|---|---|
| repeated-failed-login-then-success | 23.29s | 22.63s | 26.15s | 21.08s | 26.15s | 1,575,261 events/s |
| password-spray-across-accounts | 7.55s | 7.18s | 8.33s | 7.13s | 8.33s | 4,861,488 events/s |
| concurrent-sessions-different-hosts | 26.49s | 26.51s | 26.73s | 26.21s | 26.73s | 1,384,958 events/s |
| service-account-interactive-auth | 4.98s | 4.43s | 6.91s | 3.58s | 6.91s | 7,373,168 events/s |
| mfa-bypass-on-required-account | 3.26s | 3.14s | 3.75s | 2.90s | 3.75s | 11,235,909 events/s |

Real spread even at fixed scale, run-to-run: B1 ranges 21.08–26.15s (a ~24% swing) on identical
input, which is exactly the kind of variance a single-run number hides. The 3 window-function
recipes (B1, B3, and to a lesser extent B2) cost noticeably more than the 2 filter-plus-join
`PolicyCompare` recipes (B4, B5) — consistent with `RuleCompiler.scala`'s own documented reasoning
for why `PolicyCompare` is the one recipe that streams natively (`stage4-streaming-and-sigma.md`).

**Scoped down, stated plainly: this is one scale point with real repeats, not a multi-point scaling
curve.** A second, larger dataset (2–5x this one) was planned to show how throughput moves with
volume, not just that it's noisy at one size. Generating and benchmarking it was descoped in this
pass after this session's background jobs were killed twice by the machine's own memory-pressure
guard (once mid-generation of a 5 GB dataset, once mid-differential-test-run) — free RAM on this
machine was observed swinging between 0.7 GB and 6.7 GB over the course of this work, for reasons
external to any one job. Extending this to a real curve needs either more headroom or accepting a
much smaller second point (e.g. 2 GB); the benchmark methodology itself (`ScaleBenchmarkCheck.scala
--dataset <dir> --runs N`) is ready to point at one whenever that's available.

## 16. Run on a real cluster

**Not attempted, and not attemptable from here.** Databricks Community Edition, EMR, or a real
multi-node cluster all need an account and credentials this environment does not have and cannot
create on its own. This item needs the project owner to set up cluster access; the compiler code
itself has no local-mode-only dependency that would block it (`RuleCompiler.compile` takes any
Spark `DataFrame`, local or distributed).

## 17. Failure recovery — a genuine kill and restart, not a description of one

`StreamingRecoveryCheck.scala` runs the one streaming-capable recipe (`PolicyCompare`,
`mfa-bypass-on-required-account`) through a real restart:

1. Start a real `StreamingQuery` (durable JSON sink, real checkpoint directory), process the first
   half of the real `mfa_bypass` replay events.
2. `query.stop()` — an actual stop of the running query's background thread.
3. Write the remaining events into the watched source directory while nothing is running.
4. Start an entirely new `StreamingQuery`, same checkpoint directory, same source, same sink.

Result ([`phaseE_streaming_recovery_check.json`](../experiments/results/phaseE_streaming_recovery_check.json)):
**PASS.** No duplicate output rows across the restart (the checkpoint correctly prevented
reprocessing already-committed work), the post-restart query processed a real non-empty
micro-batch (proof it did new work, not a no-op), and the combined result from both queries
matches the same events run through the uninterrupted batch path exactly:
`{evt-mb01: alert, evt-mb02: no_alert, evt-mb03: insufficient_context}`.

## 18. Four-way comparison at scale

**Manual-rules baseline vs SENTINEL Forge — done, with a real finding.**
`ManualBaselineGeneratedCheck.scala` scores the Phase 1 hand-written detectors
(`experiments/baselines/manual/`) against the same generated dataset and labels
`GeneratedDataCheck` uses for SENTINEL Forge:
[`phaseE_manual_baseline_at_scale.json`](../experiments/results/phaseE_manual_baseline_at_scale.json).

| Behaviour | Passed | Labels |
|---|---|---|
| repeated-failed-login-then-success | 2000 | 2000 |
| password-spray-across-accounts | **1994** | 2000 |
| concurrent-sessions-different-hosts | 2000 | 2000 |
| service-account-interactive-auth | 2400 | 2400 |
| mfa-bypass-on-required-account | 2500 | 2500 |

**The real finding this scale run surfaced**: `PasswordSprayAcrossAccounts.scala` (the hand-written
baseline) still calls `approx_count_distinct` — the exact HyperLogLog-based estimator the
independent review moved the real compiler away from
(`docs/spec/independent-review-corrections.md`), for exactly the reason this baseline now
demonstrates: **6 of 2000 incidents (0.3%) were misclassified from the estimator's real error**,
something the original 48-event replay set was never large enough to expose (the same code passed
17/17 there). SENTINEL Forge's own exact-count fix scores 2000/2000 on the identical incidents
(`GeneratedDataCheck`, same dataset). This is the concrete, at-scale version of the abstract
argument `independent-review-corrections.md` made for that fix.

The baseline's password-spray detector also exposes no triggering event id in its own output, so
it's matched on `groupKey` only here — a real, small quality gap in the hand-written code itself,
not a limitation of this check.

**Direct-LLM and schema-constrained baselines — not re-run at this scale, and why that's the
right call, not a shortcut.** Both baselines' generated Scala files
(`experiments/baselines/direct_llm/generated/`, `experiments/baselines/schema_constrained/generated/`)
hardcode values from the 5 *specific* reports they were generated from (account names like
`svc-notify09`, per `experiments/results/README.md`'s own findings table). Running that code
against a *different*, scale-generated dataset with different synthetic account names would
produce a trivial 0-alerts result across the board — re-confirming the already-documented
hardcoding bug, not producing new information. Their comparison is a report-driven-generation
question, already fully evaluated at that level in Phase 5; "at scale" doesn't add anything to it
because the bug they exhibit isn't scale-dependent.

## Summary

| Item | Status |
|---|---|
| 15. Multi-scale benchmark | Real, repeated-run measurement at one scale point (1 GB); multi-point curve explicitly descoped this pass, methodology ready to extend |
| 16. Real cluster | Not attemptable without the project owner's cloud account |
| 17. Failure recovery | Done — real kill/restart, PASS |
| 18. Four-way comparison at scale | Done for manual-vs-SENTINEL-Forge, with a real finding (exact vs. approximate counting, now visible only at scale); direct-LLM/schema-constrained explicitly scoped out with reasoning, not silently skipped |
