# Phase 4 completion — streaming path + Sigma export

Closes the two items left open after the first Phase 4 pass (see
`docs/spec/stage4-scala-toolchain.md`): "run the same rule over a
streaming path" and "Sigma-format export alongside the Spark rule."

## Streaming

`compiler/src/main/scala/sentinelforge/compiler/StreamingCheck.scala` runs
a real Spark Structured Streaming query. It does **not** attempt to stream
all 3 recipes — and that boundary is a real Spark fact, not a gap:

> Non-time-based window operations
> (`Window.partitionBy(...).orderBy(...).rangeBetween(...)`) are not
> supported on a streaming DataFrame.

`SequenceThenTrigger` and `DistinctCountWithinWindow` both use exactly that
pattern (see `RuleCompiler.scala`), so a true incremental-streaming version
of either needs Structured Streaming's own time-window + watermark
`groupBy` mechanism — a genuinely different implementation, tracked as
follow-up work, not silently skipped.

`PolicyCompare` uses no window function at all (a stream-static join plus a
filter), which Structured Streaming supports natively. `StreamingCheck.scala`
calls the *exact same* `RuleCompiler.compile` used by the batch path
(`ReplayCheck.scala`) — proving the compiler's output genuinely works
unmodified on both paths, not a separate streaming-only reimplementation.

**Real run:** the 3 real `mfa_bypass` replay events were written into a
watched directory one at a time, 3 seconds apart, while the query was
already running (`Trigger.ProcessingTime("2 seconds")`). Result: **4 real
micro-batches** (proof of incremental processing, not one batch read
dressed up as streaming), and the correct `alert` / `no_alert` /
`insufficient_context` status for all 3 events — identical to the batch
result. Full output in
[`experiments/results/phase4_streaming_check.json`](../../experiments/results/phase4_streaming_check.json).

## Sigma export

`compiler/src/sigma_export.py` converts a compiled spec into real Sigma
YAML. Run for all 5 behaviours; output in `spark/sigma/*.sigma.yml`, all
verified to parse as valid multi-document YAML.

The 3 recipes map to Sigma with different fidelity, stated plainly rather
than smoothed over:

- **`DistinctCountWithinWindow`** (B2, B3) → a native Sigma `value_count`
  correlation. Clean, exact mapping.
- **`SequenceThenTrigger`** (B1) → a real Sigma feature (correlation rules
  chaining other correlation rules by name) makes this representable:
  an `event_count` correlation applies the failure threshold, then a
  `temporal_ordered` correlation chains that count with the trigger event.
  Structurally faithful, but explicitly marked "best-effort — validate
  against a real Sigma backend" in the generated file's own description,
  since it hasn't been run through an actual Sigma processing engine here.
- **`PolicyCompare`** (B4, B5) → **a genuine, permanent limitation, not a
  bug**: Sigma's specification has no mechanism for comparing a log field
  against an external reference/lookup table. The generated rule captures
  only the log-observable half of the condition (e.g. `mfa_used: false`)
  and carries an explicit `LIMITATION` field in the YAML itself explaining
  that the policy comparison needs the target SIEM's own lookup-list
  mechanism. This is stated in the artifact a security engineer would
  actually read, not just in this doc.

## Phase 4 status after this pass

All 4 original Phase 4 items are now done: compiler (batch), historical
replay, streaming (for the recipe that's structurally streamable), and
Sigma export (with honestly-scoped fidelity per recipe).

## Revisited after Phase D's correctness fixes (2026-09-22)

Phase D fixed `DistinctCountWithinWindow` to alert once per incident (a
rising edge in the rolling distinct count) instead of collapsing every
qualifying row into one row per group
(`docs/spec/detection-semantics.md`). **This does not change the streaming
limitation stated above, and in one sense sharpens it**: the fix adds a
`lag()` call over `Window.partitionBy(groupCol).orderBy(ts_long,
event_id)` — a second analytic window function, on top of the
`rangeBetween` one already there. Structured Streaming's restriction is on
analytic window functions on a streaming DataFrame at all, not specifically
on `rangeBetween`, so this recipe is no closer to streaming-compatible as
written; a real streaming version would still need Structured Streaming's
own stateful primitive (`flatMapGroupsWithState`, tracking each group's
current distinct set and breaching status as explicit state across
micro-batches) — a distinct implementation, not an incremental change to
the current one, and still scoped as real follow-up work rather than
attempted in this pass.

`PolicyCompare` is unaffected by either Phase D fix in a way that changes
its streaming status: it already streamed natively (no window function),
and the null-logField correction only changes a `when(...)` branch's
output value, not the query's shape.

**Failure recovery, tested for real**
(`compiler/src/main/scala/sentinelforge/compiler/StreamingRecoveryCheck.scala`):
a genuine kill-and-restart of the one streaming-capable recipe's query —
process the first half of a real event set, `query.stop()` (a true stop,
not a description of one), write the remaining events while nothing is
running, start an entirely new `StreamingQuery` against the same
checkpoint directory, and verify both that it resumes (processes new
micro-batches) and that the combined result exactly matches the same
events run through the batch path uninterrupted, with no duplicate output
rows from the restart. Result:
[`experiments/results/phaseE_streaming_recovery_check.json`](../../experiments/results/phaseE_streaming_recovery_check.json).
