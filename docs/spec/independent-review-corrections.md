# Independent Review Corrections (2026-09-16)

An external review of this repository identified issues across 2 rounds,
after the project's own "6/6 phases complete" summary and again after
round 1's fixes shipped. Every finding in both rounds was reproduced
independently before any fix was written. Round 1: 4 findings, 3 real bugs
fixed, 1 accurately characterized but already disclosed. Round 2: 3 more
findings (surfaced only after round 1's fixes existed to build on), all 3
real, all 3 fixed and re-verified with real execution.

This file exists because the corrections are significant enough to
document on their own, not folded quietly into other files — the same
practice this project has followed for every other bug found along the
way (see `experiments/results/README.md`'s timestamp-parsing bug,
`dashboard/README.md`'s 4 UI bugs).

## 1. The complete report-to-detection path was missing — CONFIRMED, FIXED

**The finding:** every Scala entry point (`ReplayCheck`, `StreamingCheck`,
`RobustnessCheck`, `ThroughputCheck`) loaded a **hand-authored**
`data/samples/ir/compiled/*.json` file by hardcoded `behaviourId`. Nothing
converted a real NLP extraction into that format. The "17/17 passed"
result proved the *compiler* was correct given a valid spec — not that
extraction-to-detection worked end to end. Reproduced by grepping every
`.scala` file for `CompiledSpec.load`: confirmed, no exceptions.

**The fix:** `compiler/src/spec_bridge.py` — converts a Stage-3-validated
extraction spec into a real `CompiledSpec`. Consistent with the project's
closed-recipe design (`docs/spec/compiled-spec-format.md`): each known
behaviour has a fixed recipe *shape* (which recipe, which grouping key,
which event types — inherent to what the compiler expresses, not
report-derived), and the bridge fills that template with the actual
extracted threshold/time-window numbers. It raises `UnbuildableSpecError`
rather than guessing if a recipe needs a number extraction didn't provide.

**Proof, not just code:** `compiler/test/evaluate_end_to_end.py` ran the
real transformer extractor + fixed Stage 3 + the new bridge on all 5
held-out reports — all 5 extracted, validated, and bridged successfully.
`EndToEndCheck.scala` then loaded those **extraction-derived** compiled
specs (not the hand-authored ones) and ran the same real-data, real-labels
check `ReplayCheck.scala` uses. Result: **17/17**, using specs that came
from a real report through a real LLM call through real validation through
real compilation. Full chain, actually run.
[`experiments/results/phase_end_to_end_replay_check.json`](../../experiments/results/phase_end_to_end_replay_check.json).

## 2. The validator accepted invalid specifications — CONFIRMED, FIXED

**The finding, reproduced exactly:**
- `validate({})` → `supported` (an empty spec asking for nothing)
- `validate({"behaviourId": "totally-made-up-behaviour", "requiredFields": ["account_id"], ...})` → `supported` (the old check only caught the `"unrecognized:"` string-prefix convention one extractor happens to use, not an actual registry check)
- `validate({..., "threshold": {"failureCount": -5}, "timeWindow": {"amount": -2, ...}})` → `supported` (numeric values were never sanity-checked at all)

**The fix**, in `compiler/src/observability_checker.py`:
- `behaviourId` is now checked against the real registry (`nlp/src/schema_fields.BEHAVIOUR_IDS`), imported directly rather than duplicated
- a spec naming zero fields is rejected outright
- `threshold`/`timeWindow` values are checked for positivity, with a new `invalidValues` result field kept separate from `missingFields` (a threshold isn't a field name; conflating them would have been its own small bug)

All 4 cases now correctly reject with a specific reason each; re-verified
against all 17 of the checker's existing passing cases (`compiler/test/evaluate_stage3.py`) — still 17/17 after the fix. One of those 17 broke
during the fix (the Phase 0 canonical IR case, because the *test harness*
never passed `behaviourId` into the dict it built) — a real bug in the
test itself, fixed alongside.

## 3. The dashboard's field-unavailability simulation was backwards — CONFIRMED, FIXED

**The finding, reproduced exactly:** unchecking a field removed it from
`spec.requiredFields` — i.e. it made the detection need less, not made the
schema provide less. Unchecking every field produced an emptied spec that
trivially validated as `supported`, the opposite of what the control
claims to demonstrate ("simulate a field becoming unavailable").

**The fix:** `observability_checker.validate()` now accepts an
`unavailable_fields` parameter — fields to treat as absent from the
schema regardless of what's actually there. `dashboard/backend/main.py`
now passes the dashboard's `remove_fields` there instead of stripping them
from the spec. The spec still asks for what it always asked for; Stage 3
now correctly rejects when what's asked for isn't available. Verified via
direct API call: removing all 3 required fields now returns `rejected`
with a specific "is being simulated as unavailable" note per field, and
the no-removal case still returns `supported` as before.

## 4. Streaming and scale are only partially demonstrated — CONFIRMED, ALREADY DISCLOSED

This was accurately described, and was already stated plainly in
`docs/spec/stage4-streaming-and-sigma.md`: only `PolicyCompare` streams
(Spark genuinely doesn't support the window functions the other 2 recipes
use, on a streaming DataFrame), and in `experiments/results/README.md`:
the 48-event, single-machine throughput numbers are explicitly captioned
"a real number for this data, not a production-scale claim." Restating it
here because the review's overall framing — that summarizing this project
as "6/6 complete" without resurfacing these caveats every time overstates
it — is fair even where the underlying fact was already written down
somewhere. Not fixed in this pass; still open, same as before.

---

## Round 2 (same reviewer, same day, after round 1's fixes shipped)

Three more findings, all reproduced before any fix, same discipline as
round 1.

### 5. Validation could approve a rule requiring data it never even asked for — CONFIRMED, FIXED

**The finding, reproduced exactly:** a spec for
`repeated-failed-login-then-success` naming only `["timestamp"]`, with
`account_id` marked unavailable — `account_id` isn't even in the
*requested* fields, so the per-field loop never looks at it, and the spec
validated `supported`. The bridge then happily compiled a rule grouping
by `account_id` anyway (from the fixed recipe template), a field the
validated spec never claimed to need.

**Why round 1's fix didn't catch this:** round 1 only ever checked fields
that WERE requested. It never asked whether the request was *complete*.

**The fix:** `spec_bridge.structural_dependencies(behaviour_id)` — since
each known behaviour's recipe shape is fixed (`RECIPE_TEMPLATES`), its
field dependencies (grouping key, `event_type`, `timestamp` for windowed
recipes, the `account_id` join key `RuleCompiler.scala` hardcodes for
`PolicyCompare`) are fully knowable in advance. `observability_checker.py`
now cross-checks these against the request itself, not just against
availability. This closes the specific "permanent boundary" claimed in
round 1's docstring — it was wrong once the closed-recipe design existed;
the fix just hadn't been connected back to Stage 3 yet.

**A real, immediate consequence:** re-running the checker's own test suite
after this fix surfaced 3 cases that had been silently passing:
`classical` misclassifying SF-SAMPLE-011 (a documented keyword-collision
bug) and dropping `mfa_used` on SF-SAMPLE-015 (a documented negation-window
bug), and `transformer` dropping `policy.expected_auth_method` on
SF-SAMPLE-014 (documented run-to-run variance). None of these were new
bugs — they were always-present extraction weaknesses Stage 3 could not
previously see. The test suite's hardcoded `"supported"` expectation for
live extractor output was itself wrong for the same reason and was
replaced with a dynamically-computed expectation
(`evaluate_stage3.py`'s `expected_status()`, independently using the same
structural-dependency facts rather than calling the checker being tested).

### 6. Numeric conversion could silently change detection behaviour — CONFIRMED, FIXED

**The finding, reproduced exactly, all three:**
- threshold `0.5` → accepted, compiled to `0` (`int(0.5)`)
- window `1.5` minutes → compiled to `60`s instead of `90`s (`int(amount) * multiplier`, truncating before multiplying)
- window missing `"amount"` → `KeyError` crash in `observability_checker.py`'s own round-1 fix (`block[value_key]` with no existence check)

**The fix**, in `spec_bridge.py`: a threshold must be a whole number now
(`float(v).is_integer()`), or it's rejected with a message stating plainly
that the *extracted value itself* is wrong, not just its representation.
Window-to-seconds conversion now multiplies before rounding
(`round(amount * multiplier)`), so `1.5` minutes correctly becomes `90`.
In `observability_checker.py`: malformed threshold/timeWindow (missing
key, wrong type, non-positive, non-integer count) are now checked with
`.get()`/`isinstance()` before any indexing, producing a structured
rejection instead of a crash — and Stage 3 now rejects a fractional
threshold too, so a bad extraction fails at validation time, not only if
it happens to reach the bridge.

### 7. The new evaluation harness could reuse stale output — CONFIRMED, FIXED

**The finding, confirmed by inspection:** `evaluate_end_to_end.py` wrote
every compiled spec to one shared directory, filed under the *predicted*
`behaviourId` — not the report name. Two reports landing on the same
behaviourId in one run would silently overwrite each other; a failed
report simply left whatever file was already there from a previous run,
indistinguishable from a fresh one; and `EndToEndCheck.scala` loaded that
shared directory by a fixed naming convention with no way to know if any
of it was current.

**The fix:** every run gets its own timestamped directory
(`compiled_from_extraction/run_<UTC-timestamp>/`); every compiled spec is
filed under its **report name**, never the predicted behaviourId, so
same-run collisions are structurally impossible regardless of
misclassification; a `manifest.json` is the one source of truth mapping
report → behaviourId → compiled-spec path; the script hard-stops
(non-zero exit) the instant any required report fails extraction,
validation, or bridging, writing a `status: "FAILED"` manifest for
inspection but never touching the "latest successful run" pointer; and
`EndToEndCheck.scala` now reads that pointer and manifest explicitly
rather than assuming a naming convention.

**Proof, not just code — and an honest complication found while proving
it:** re-running the full 5-report harness after this fix hit the round-2
finding #5 fix working exactly as intended: `service-account-auth-003`'s
real extraction consistently (5/5 attempts) omitted
`policy.expected_auth_method`, and the hard-stop correctly refused to
produce a stale/partial "successful" run rather than silently limping
past it — precisely the behaviour requested. This is real, additional,
previously-invisible evidence of the same documented extraction
reliability gap (`nlp/README.md`'s run-to-run variance), now surfaced
consistently instead of intermittently, for two of the five held-out
reports. Fixing that reliability is a real, separate piece of work — not
done here, and explicitly not folded into this round's scope, which was
the harness's directory/manifest/hard-stop behaviour, not extraction
quality. To prove the harness machinery itself (not extraction
reliability) with a real, clean, all-successful run: a `--reports` option
was added to run a named subset, used to run the 3 reports that succeeded
consistently across every attempt
(`login-brute-force-003`, `password-spray-003`, `concurrent-sessions-003`)
— manifest written, `LATEST_SUCCESSFUL_RUN.txt` updated, and
`EndToEndCheck.scala` against that real manifest: **11/11**. This also
caught one more real bug: the spec files were being written
pretty-printed, which silently broke `CompiledSpec.scala`'s single-line
JSON reader — fixed alongside.

## What this changes about how "done" should be read

The corrected, precise claim: the compiler, extractors, validator, and
dashboard are each independently real and now more correct than before —
and, as of the fixes above, there is now one genuine, verified,
independently-checkable execution of the full path from a held-out
report's text through to a correct compiled detection. That is a real and
new result. It does not mean every component was already wired together
by default before this correction, and it does not mean streaming or
distributed-scale claims extend beyond what's actually been run.

**After round 2, one more precise thing to say — updated after further
testing, since the first version of this note undersold it:** Stage 3's
validator now catches real, previously-invisible extraction weaknesses
often enough that a clean, all-5-reports successful run of
`evaluate_end_to_end.py` is not currently reliable on the first try.
Initial testing pinned this to 2 specific reports
(`service-account-auth-003`, `mfa-bypass-003`, the two `PolicyCompare`
behaviours) failing consistently (5/5 observed attempts for the former).
Re-running the 3-report subset that had "consistently succeeded" later
caught a **third** report (`password-spray-003`, a `DistinctCountWithinWindow`
behaviour) failing the same way on a subsequent attempt — so the gap is
broader than "2 specific reports," and closer to "any report, with
uneven but real frequency," consistent with the run-to-run variance
`nlp/README.md` already documented for the transformer extractor
generally. The honest, current statement is: this project's transformer
extraction is not yet reliable enough to guarantee a first-try clean run
across all 5 held-out reports, and Stage 3 now correctly refuses to hide
that instead of silently compiling an incomplete spec. A repo-committed
successful run (`data/samples/ir/compiled_from_extraction/`,
`LATEST_SUCCESSFUL_RUN.txt`) required 2 attempts at the 3-report subset to
obtain. Improving extraction reliability is real, separate, tracked
follow-up work — deliberately not done in this round, which was scoped to
the validator/bridge/harness bugs the review named,
not to extraction quality.

## Regression tests (2026-09-17)

Every fix above had been verified once, via an ad-hoc script, then
reported — never saved as something that runs again. That gap is closed:

- `compiler/test/test_observability_checker.py` — 11 cases, covering every
  round-1 and round-2 validator finding (empty spec, unknown behaviourId,
  negative/fractional threshold, malformed timeWindow, the structural
  dependency check, the dashboard's unavailable-fields simulation).
- `compiler/test/test_spec_bridge.py` — 9 cases, covering the numeric
  conversion bugs (fractional threshold, window truncation, missing
  `amount`) and the structural dependency helper itself.
- `compiler/test/test_evaluate_end_to_end.py` — 5 cases, using synthetic
  injected results (no real LLM calls, no network) to test the harness
  logic in isolation: same-run filename collisions, a failed run never
  updating the success pointer, a partial-success run still being marked
  `FAILED` overall, two separate runs never colliding, and the
  single-line JSON format `CompiledSpec.scala` requires. Required a small
  refactor of `evaluate_end_to_end.py` (`execute_run()` pulled out of
  `main()` with an injectable `run_one_fn`) to make this possible without
  hitting the network.

All 25 cases pass as of this writing. Run any of them directly:
`python compiler/test/test_observability_checker.py`, etc.

## Correctness standard follow-up (2026-09-17): exact vs. approximate distinct counts

A further, more detailed completion standard (not from the external
review — from the project owner directly) required: "use exact distinct
counts for detection thresholds unless an approximation is explicitly
justified and evaluated." Checking the compiler against that standard
found a real violation, not yet flagged by either review round:

`RuleCompiler.scala`'s `DistinctCountWithinWindow` recipe — used by
`password-spray-across-accounts` and `concurrent-sessions-different-hosts`,
both threshold-triggered detections — called
`approx_count_distinct(col(distinctCol)).over(byGroupTime)`, Spark's
HyperLogLog-based approximate cardinality estimator, to decide a hard
`>= threshold` boundary. No justification or evaluation for that
approximation existed anywhere in the project; it had simply been the
natural function to reach for once `count(distinct x)` turned out not to
be usable directly as a Spark window aggregate (Spark's window-function
framework does not support distinct aggregates the way `approx_count_distinct`
happens to be special-cased to allow).

**Fix**: replaced the approximate count with an exact one —
`size(collect_set(col(distinctCol)).over(byGroupTime))`. `collect_set` is
a valid Spark window aggregate (it does not hit the same restriction as
`count(distinct x)`), and `size()` of the resulting exact set gives an
exact distinct count with no estimation error, at the cost of one
constructed array per row inside the window instead of a fixed-size HLL
sketch — a real memory/performance tradeoff at large per-key cardinalities
that this project's data sizes don't currently exercise, and that would
need its own measurement before being called free at scale.

**Verification, not just a claim**:
- `sbt compile` — clean.
- `sbt "runMain sentinelforge.compiler.ReplayCheck"` — still 17/17
  against the real Phase 1 data and labels, including both
  `DistinctCountWithinWindow` behaviours' 5 scenarios
  (`SPRAY-POS`/`SPRAY-NEG`/`CS-POS`/`CS-NEG-SAMEHOST`/`CS-NEG-WINDOW`),
  written to `experiments/results/phase4_replay_check.json`.
- `sbt "runMain sentinelforge.compiler.EndToEndCheck"` — still 11/11
  against the real extraction-derived run, written to
  `experiments/results/phase_end_to_end_replay_check.json`.
- New permanent regression check,
  `compiler/src/main/scala/sentinelforge/compiler/DistinctCountBoundaryCheck.scala`
  (`sbt "runMain sentinelforge.compiler.DistinctCountBoundaryCheck"`),
  built specifically to exercise the boundary an approximation error
  would show up at and the failure mode the *opposite* naive
  implementation (`count(*)` instead of a true distinct count) would hit:
  exactly threshold−1 distinct accounts against one host (no alert),
  exactly threshold distinct accounts (alert), and threshold−1 distinct
  accounts spread across duplicate-laden events, i.e. more total events
  than the threshold but not more *distinct* accounts (no alert — this
  is the case a plain event count would get wrong). All 3 pass; written
  to `experiments/results/distinct_count_boundary_check.json`.

## Correctness standard follow-up (2026-09-17): behaviour-specific threshold key names

The same newest standard also required "behaviour-specific threshold
names" — `spec_bridge.py`'s `_extract_threshold_value` previously took
"whichever numeric value is there" from an extraction's `threshold` dict,
with no check that its key name actually meant this behaviour's count.
That would silently compile a rule from a threshold extracted under a key
name belonging to a *different* behaviour.

**First attempt was wrong, caught before shipping it**: the first fix
hardcoded one exact expected key name per behaviour, sourced from
`data/samples/ir/gold/*.gold.json` (`failureCount`,
`distinctAccountCount`, `successCount`). Before trusting that, it was
checked against a **live run of the real extractor**
(`nlp/src/transformer_extractor.py`) on the 3 held-out counting reports —
not assumed from the gold files alone. `repeated-failed-login-then-success`
and `password-spray-across-accounts` matched their gold key exactly, but
`concurrent-sessions-different-hosts` came back `{"loginCount": 2}`, not
gold's `{"successCount": 2}` — a reasonable synonym, not a mislabelling.
A single hardcoded name would have made the validator reject this real,
correct extraction, breaking the actual working pipeline. Caught before
shipping, not after.

**Fix**: `EXPECTED_THRESHOLD_KEYS` maps each behaviour to the *set* of key
names actually observed to mean its threshold (adding `loginCount`
alongside `successCount` for concurrent-sessions), and
`_extract_threshold_value` requires the threshold dict to contain one of
them — still rejecting a key name that belongs to a different behaviour
(e.g. `distinctAccountCount` on `repeated-failed-login-then-success`),
which is the actual error this exists to catch.

**Verification**: `test_spec_bridge.py` gained
`case_threshold_under_wrong_behaviours_key_name_rejected` and
`case_threshold_accepts_real_transformer_synonym` (11/11 pass). Beyond
unit tests, a **fresh, live** `python compiler/test/evaluate_end_to_end.py
--reports login-brute-force-003 password-spray-003 concurrent-sessions-003`
run (new LLM calls, new Stage 3 validation, new bridge run, not replaying
old artifacts) succeeded for all 3 reports under the stricter check, and
`sbt "runMain sentinelforge.compiler.EndToEndCheck"` against that brand-new
run scored 11/11 real scenarios.

## Correctness standard follow-up (2026-09-17): formal detection semantics

The standard's remaining item-1 sub-requirement — "define exact detection
semantics: window boundaries, event ordering, repeated incidents, missing
values, policy lookup failures" — had no formal answer anywhere in the
project. Closed via a new permanent check,
`compiler/src/main/scala/sentinelforge/compiler/DetectionSemanticsCheck.scala`,
that constructs 8 small real cases and observes `RuleCompiler`'s actual
behavior (not asserted behavior), written up in the new
`docs/spec/detection-semantics.md`. Every claim in that document traces to
one of these 8 executed cases (`experiments/results/detection_semantics_check.json`).

This surfaced 2 real, previously undocumented correctness gaps, kept open
as named follow-up rather than silently fixed or silently left
undiscovered:
1. `DistinctCountWithinWindow` collapses ALL qualifying rows for a
   groupKey into a single alert row for the ENTIRE dataset's timespan —
   two genuinely separate incidents for the same host, a day apart, both
   independently crossing the threshold, produce one alert whose
   `windowStart`/`detectedAt` misleadingly span the full day, unlike
   `SequenceThenTrigger`, which correctly alerts once per qualifying
   incident.
2. `PolicyCompare`'s `falseWhenRequired` comparison treats a `NULL`
   per-event log field (e.g. `mfa_used` present in the schema but null on
   a specific event) the same as "condition not met" (three-valued SQL
   logic), silently classifying an unobserved value as compliant
   (`no_alert`) instead of `insufficient_context` — only a missing
   *policy* row is currently caught that way, not a missing *log* value.

Fixing either is a real, scoped design decision (especially #1, which
would change output row shape and needs re-verification against every
result that depends on it) — deliberately not done in this pass, to keep
this pass about *defining and testing* the semantics, not silently
changing them.

This does not by itself close out the full "correctness" completion-standard
item — a small independently-implemented reference detector for
differential boundary-case testing remains open, tracked separately.
