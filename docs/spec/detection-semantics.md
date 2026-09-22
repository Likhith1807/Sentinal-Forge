# Detection Semantics (v1)

`docs/spec/compiled-spec-format.md` defines *what* each of the 3 recipes
computes. This document defines the precise *edge-case behavior* the
project's correctness completion standard requires: window boundary
inclusivity, event ordering, repeated-incident alert cardinality,
missing-value handling, and policy-lookup-failure handling.

**Every claim below is backed by a real, executed case, not inferred from
reading the code** —
`compiler/src/main/scala/sentinelforge/compiler/DetectionSemanticsCheck.scala`
(`sbt "runMain sentinelforge.compiler.DetectionSemanticsCheck"`) constructs
each scenario as a small in-memory Spark DataFrame, runs it through the
real `RuleCompiler`, and records the actual output. Results:
`experiments/results/detection_semantics_check.json`. Where a case exposed
a real gap (not just a documentation gap), that's stated plainly below —
this document does not smooth over what the compiler doesn't do.

## Window boundaries: closed on both ends

`SequenceThenTrigger` and `DistinctCountWithinWindow` both use
`Window.partitionBy(groupingKey).orderBy(parseTs(timestamp)).rangeBetween(-timeWindowSeconds, 0L)`.
Spark's `rangeBetween` is a **closed interval**: a row exactly
`timeWindowSeconds` before the current row's timestamp **is** included.

Verified (case A/B): with `timeWindowSeconds=300` and `countThreshold=3`,
3 failures at `t=0,1,2` followed by a success at exactly `t=300` **does**
alert (the `t=0` failure is still in range). The same shape with the
success moved to `t=301` does **not** alert — `t=0` has fallen out of
range, leaving only 2 qualifying failures, below threshold.

## Event ordering: whole-second precision, not millisecond

The schema documents millisecond-precision timestamps
(`authentication-log-schema.md`), but `RuleCompiler.parseTs(...)` parses
into a `timestamp` type and the window's `orderBy` casts it `.cast("long")`
— **truncated to whole seconds**. Two events 400ms apart within the same
second are ordered arbitrarily relative to each other (tied order key),
though both still correctly fall inside or outside a window based on
which whole second they land in.

Verified (case C): 3 failures within the same second, followed by a
success 100ms into the next second, still alerts correctly. This is not
a case where sub-second precision changes the *outcome* — the compiler's
`timeWindowSeconds` granularity is already whole seconds by construction
— but a future recipe using sub-second thresholds would need this fixed
first. Documented, not currently a functional defect at the seconds
granularity the compiler is designed for.

## Repeated incidents: alert cardinality is consistent across all 3 recipes (fixed, Phase D)

All 3 recipes now alert **once per qualifying incident** — this was not always true, and the
inconsistency was the most consequential finding of the first pass of this document:

- **`SequenceThenTrigger`**: alerts once per qualifying trigger event. Two fully separate
  incidents for the same account, a day apart, each meeting the threshold independently, produce
  **2 alert rows** (case D). Always correct; not touched by this fix.

- **`PolicyCompare`**: alerts once per qualifying event, with no deduplication at all. Two
  separate non-compliant `login_success` events for the same account produce **2 alert rows**
  (case H) — each event is its own policy violation. Always correct; not touched by this fix.

- **`DistinctCountWithinWindow`**: **CORRECTION (Phase D).** This used to alert at most once per
  groupKey *ever*, regardless of how many separate incidents occurred — a final `.groupBy(groupKey)`
  collapsed every qualifying row into one output row, taking `min(timestamp)` as `windowStart` and
  `max(timestamp)` as `detectedAt`. Two fully separate password-spray bursts against the same
  host, a day apart, produced **1 alert row** spanning the full day, falsely implying one
  continuous incident.

  **The fix**: alert on the *rising edge* of the rolling distinct count — the instant it first
  reaches the threshold, exactly the same per-trigger-event granularity `SequenceThenTrigger`
  already had. `windowStart` is now the alert's own `detectedAt` minus the window duration (well
  defined per alert, not `min()` over however many incidents happened to share a groupKey). This
  is also exactly the "episode" semantics `scripts/datagen/refdetect.py`'s independent reference
  implementation used from the start (it was written to the *intended* semantics, not the bug).
  Re-verified with case E: the same two-incidents-a-day-apart scenario now produces **2 alert
  rows**, each with its own `detectedAt` and a `windowStart` exactly `windowSecs` before it —
  and every existing real-data result that depends on this recipe's output shape was re-run and
  still passes (`ReplayCheck` 17/17, `EndToEndCheck` 11/11, `ManualBaselineCheck` 17/17,
  `DistinctCountBoundaryCheck` all pass, plus the 10,900-label `GeneratedDataCheck` run — see
  `docs/data-sources.md`).

## Missing/malformed values: silent exclusion, not a structured error

Two distinct "missing" cases were tested, with two distinct (both
currently silent) outcomes:

- **Malformed timestamp value** (case F): an event with a
  non-timestamp `timestamp` string is not rejected and does not crash —
  `parseTs` (`to_timestamp` with an explicit format) returns `null` for
  it, and the row's window frame is then keyed off that lost order
  value. Observed effect: the malformed row's own contribution to
  *other* rows' `recent_count` windows is silently dropped (a count that
  should have been 3 was computed as 2, correctly *not* alerting for the
  wrong reason — no timestamp to place it in range with). **This is a
  real gap**: Stage 3 validates that a required field is *present in the
  schema*, but nothing validates that a field's *value*, once it reaches
  Spark, actually parses. A malformed timestamp degrades detection
  silently rather than surfacing as a structured error the way a missing
  *column* does (`RobustnessCheck`'s Test 2 — dropping the column
  entirely fails loudly with a `AnalysisException`; a malformed *value*
  inside a present column does not).

- **Null value in a present field** (cases G, I): **CORRECTION (Phase D).** `PolicyCompare` used
  to treat a `NULL` `logField` (e.g. `mfa_used` or `auth_method` present in the schema but null on
  a specific event) the same as "condition not met" — three-valued SQL logic makes both
  `NULL === false` (`falseWhenRequired`) and `NULL =!= x` (`notEqual`) evaluate to `NULL`, which
  `.otherwise(...)` then read as `no_alert`. Only a missing *policy* row was caught as
  `insufficient_context`; a null *log* field was silently treated as compliant, on **both**
  comparison operators — case G originally tested only `falseWhenRequired`; case I, added in this
  pass, confirms the same gap existed in `notEqual` too.

  **The fix**: `logField.isNull` is now checked explicitly, before the comparison ever runs, for
  both operators — a genuinely unobserved log value now correctly degrades to
  `insufficient_context` instead of being read as compliant. Re-verified: case G now observes
  `status=insufficient_context` (was `no_alert`); case I (new) observes the same for `notEqual`.
  `scripts/datagen/refdetect.py`'s independent reference implementation had the equivalent gap
  (a null `mfa_used`/`auth_method` fell through to no alert being appended at all, and for
  `auth_method` specifically, Python's plain `!=` on `None` would have produced a **false alert**,
  not just a false negative) and was fixed the same way.

## Policy lookup failure: per-event, not per-rule

`PolicyCompare` left-joins on `account_id` per event, so a policy lookup
failure (account absent from the policy reference) affects only that
account's own rows — it does not degrade or block evaluation for other
accounts sharing the same rule. Already covered by
`RobustnessCheck`'s Test 1 (whole policy table empty → all evaluated
accounts correctly degrade to `insufficient_context`) and
`ReplayCheck`'s `SA-NEG-NO-POLICY`/`MFA-NEG-NO-POLICY` scenarios (a
specific account missing from an otherwise-populated policy table
correctly resolves to `insufficient_context` while other accounts in the
same run resolve normally).

## What this document does and doesn't close

This closes the "define exact detection semantics" requirement for the
behavior that exists today — every claim above is pinned down by a
permanent, real, executable check
(`DetectionSemanticsCheck.scala`), not just prose. It does **not** claim
the underlying behavior is already ideal: the `DistinctCountWithinWindow`
cross-incident collapsing and the null-logField-as-compliant gap are both
real, named limitations kept open as tracked follow-up rather than
silently fixed or silently left undocumented.

The still-open "independently-implemented reference detector" requirement
(a small, non-Spark reimplementation of the 3 recipes to differentially
test generated boundary cases against) is separate, larger follow-up work
and is not addressed by this document.
