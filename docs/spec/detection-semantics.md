# Detection semantics (v2)

This is the contract the compiled rules obey. It is written from the intended behaviour, and three
implementations are held to it:

* the Spark batch executor (`RuleCompiler.scala`, run by `RunRule`),
* the Spark Structured Streaming executor (`StreamingEngine.scala`, run by `RunStreaming`),
* an independent, deliberately naive Python reference engine (`sentinelforge/refengine.py`).

Every rule below is pinned by a test that states the rule in its name. Agreement between the engines is
tested (differential, streaming), but agreement alone cannot catch a misunderstanding all of them share, so
the golden cases in `tests/core/test_refengine.py` and `RuleCompilerSpec.scala` hold expectations worked out
by hand from this document.

## 1. Events

**Timestamps** are RFC 3339 with an explicit zone (`Z` or `+hh:mm`) and 0-6 fractional digits, parsed to exact
integer microseconds since the epoch. Zone offsets are normalised to UTC. Anything else - no zone
(`2026-03-01T00:00:03`), a date-only string, seven fractional digits, `2026-13-40...`, an empty string - is
**malformed**. A missing timestamp is `null_timestamp`. *(v1 truncated to whole seconds, which made
`00:00:00.999` and `00:00:01.000` indistinguishable to window arithmetic.)*

**Quarantine.** A row is quarantined - counted, listed with a reason, and excluded from evaluation - if its
`event_id` is null (`null_event_id`), its timestamp is missing or malformed, or a column the rule groups or
counts by is null (`null_<column>`). Quarantined rows are never silently dropped and never sorted as if they had
a timestamp.

**Duplicates.** Rows sharing an `event_id` collapse to one: the earliest instant, ties broken by the
lexicographically smallest row content (other columns sorted by name, NULLs skipped, joined by U+0001). A
collector that redelivers an event therefore cannot inflate a count or duplicate an alert. *(Streaming keeps the
first-delivered copy instead; identical for exact redelivery, different for conflicting duplicates - see 5.)*

**Schema.** A rule refuses to run - with a structured error naming every missing or mistyped column - if the
data lacks a column the rule reads or emits, or has it with the wrong type (`mfa_used` as a string, say). A
column that is present but entirely NULL is *unobserved*, not missing: its rows become `insufficient_context`.

## 2. Windows

Closed on both ends: an event at exactly `t - W` is inside a window ending at `t`. Comparison is in
microseconds. Events with an **identical timestamp** are all inside each other's window regardless of their order.
`W` is a whole number of seconds between 1 and 7 days (validated; `0.5 s` is rejected as unsupported precision).

## 3. Recipes

**SequenceThenTrigger** (repeated failed logins, then success). For every trigger event (`login_success`), count the
counting events (`login_failure`) of the same group inside `[t - W, t]`; alert if the count is `>= N`. One alert per
trigger event. `matchedCount` and the evidence list are that count and those events.

**DistinctCountWithinWindow** (failures across accounts from one host; successes from several hosts for one account).
Among events of the filter type for one group, the distinct count of the distinct field inside `[t - W, t]` is
computed **exactly** (never approximated). An alert fires at the first event, ordered by `(timestamp, event_id)`,
at which the count reaches `N` after an event at which it did not: **one alert per incident** (a "rising edge").
Two incidents for the same entity separated by a non-breaching event alert twice. `N >= 2` (a threshold of 1 is
true of every event and makes "once per incident" meaningless; it is rejected at validation).

**PolicyCompare** (authentication method / MFA). One result per `login_success`:

| Situation | status | reason |
|---|---|---|
| no policy record for the account | `insufficient_context` | `no_policy_record` |
| policy rows for the account conflict | `insufficient_context` | `policy_conflict` |
| the compared policy value is NULL | `insufficient_context` | `policy_value_null` |
| the log value is NULL (unobserved) | `insufficient_context` | `log_value_null` |
| the comparison holds | `alert` | `policy_violated` |
| otherwise | `no_alert` | `compliant` |

A NULL is never read as compliant. **Duplicate policy rows** for one account collapse when identical and make the
account `policy_conflict` when they differ - a join is never multiplied by duplicate policy rows.

**Versioned policy.** If the policy table has an `effective_from` column, the row in force *at the event's own
timestamp* applies: the latest `effective_from <= event time`. A null `effective_from` means "always in force"; an
unparseable one is ignored. A change is not retroactive and need not arrive before the events it governs (in
batch). Each result records the `policyVersion` it used.

## 4. What `matchedCount`, `windowStart` and `evidence` mean

`windowStart = t - W` rendered as `yyyy-MM-ddTHH:mm:ss.SSSZ` (UTC, milliseconds, truncated - not rounded);
`matchedCount` is the count that met the threshold; `evidence` lists the events inside `[t - W, t]` that were
counted, ordered by `(timestamp, event_id)`, each with its own timestamp and value.

## 5. Streaming (`RunStreaming`)

Streaming produces the batch answer under an explicit, testable policy - not "eventually correct":

* **Lateness.** Per key, an event is *finalised* (evaluated, in timestamp order) once it is at least `lateness`
  older than the newest event that key has seen. An event at or before the key's finalised time cannot be placed in
  order and is emitted as a `late` record - never silently dropped. **Agreement with batch is defined over the input
  minus the events reported late** (`experiments/results/streaming_agreement.json`).
* **Latency** is therefore at least `lateness` after a key's newest event, plus the trigger interval. Set
  `lateness = 0` for in-order sources.
* **Duplicates.** An id already buffered or inside the retained window is emitted as `duplicate`; an older
  redelivery surfaces as `late`. Beyond state expiry a redelivery is undetectable.
* **State expiry.** Requires `expiry >= window + lateness`. The Spark watermark is `max event time - expiry`; a key's
  buffer is flushed and its state dropped when global event time is `expiry + lateness` past the key's newest event.
  With that inequality an expired key holds nothing a later event could need, so expiry cannot change an alert;
  violate it and it can (the runner refuses to start). Rows older than the global watermark are dropped **by
  Spark** and only *counted* (`rowsDroppedByWatermark` in `stream.json`), not identified.
* **Tail flush.** An idle stream holds back each key's last `lateness` of events until later events, or a
  heartbeat row (`event_type = "__flush__"`, any valid timestamp), advance the watermark.
* **Policy** is re-read every micro-batch and applied by event time (section 3); a policy update is never retroactive
  to results already emitted.
* **Output** is written from `foreachBatch`, one file per kind per batch id, atomically. Replaying a batch after a
  crash overwrites the same file. That is *effectively-once output under conditions* - immutable, retained source
  files; a deterministic state function; checkpoint and output directory preserved together; one query per
  checkpoint - and **not** an unconditional exactly-once claim. Losing the checkpoint but not the output re-emits
  alerts. See `experiments/results/streaming_recovery.json` for what was actually tested.

## 6. Where each rule is tested

| Rule | Test |
|---|---|
| microsecond window edges, closed interval | `RuleCompilerSpec` "timestamp precision"; `test_refengine.py::TestSequenceThenTrigger`; differential |
| quarantine reasons, zone normalisation | `RuleCompilerSpec` "malformed timestamps", "numeric UTC offset"; `TestTimestamps` |
| duplicate `event_id` | `RuleCompilerSpec` "duplicate delivery"; metamorphic "redelivery changes no alert" |
| duplicate / conflicting / versioned policy | `RuleCompilerSpec` "duplicate policy rows"; differential (policy scenarios) |
| exact distinct counts, rising edge | `TestDistinctCount`; mutation check kills "events-not-distinct" and "no-rising-edge" |
| schema mismatch | `RuleCompilerSpec` "structured schema error"; `tests/integration` |
| batch = stream under the lateness policy | `scripts/verify/streaming_agreement.py` (8 regimes) |
| crash recovery, retry | `scripts/verify/streaming_recovery.py` |
