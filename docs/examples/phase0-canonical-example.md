# Phase 0 — Canonical Example

Everything in SENTINEL Forge's real pipeline (Phases 1-6) is validated
against this one hand-built, end-to-end example: a single behaviour, worked
through every stage by hand, before any model or compiler exists to do it
automatically. If a later component can't reproduce this, it isn't ready.

## The pieces

| Stage | Artifact | Path |
|---|---|---|
| Source report | Synthetic CTI report, SF-SAMPLE-001 | [`data/samples/reports/login-brute-force-001.md`](../../data/samples/reports/login-brute-force-001.md) |
| Log schema | Authentication log schema v1 (human-readable) | [`docs/schema/authentication-log-schema.md`](../schema/authentication-log-schema.md) |
| Log schema | Same schema, machine-readable | [`data/samples/schema/authentication_log_schema.json`](../../data/samples/schema/authentication_log_schema.json) |
| IR format | Typed behaviour IR spec | [`docs/spec/behaviour-ir-format.md`](../spec/behaviour-ir-format.md) |
| IR instance | Hand-authored IR for this behaviour | [`data/samples/ir/login-brute-force-001.json`](../../data/samples/ir/login-brute-force-001.json) |
| Target rule | Golden-reference Spark detection | [`compiler/test/golden/LoginBruteForceThenSuccess.scala`](../../compiler/test/golden/LoginBruteForceThenSuccess.scala) |
| Replay data | Labelled synthetic events | [`data/samples/replay/login_brute_force_events.jsonl`](../../data/samples/replay/login_brute_force_events.jsonl) |
| Replay labels | Independent ground truth | [`data/samples/replay/login_brute_force_labels.json`](../../data/samples/replay/login_brute_force_labels.json) |

## The behaviour, end to end

> Five or more failed sign-ins for an account, followed immediately (within
> 2 minutes) by a successful sign-in for the same account.

**Report → Schema.** The report names three things the detection needs
(account, outcome, time) and one thing it explicitly should *not* depend on
(`source_ip`, flagged as unreliable). The schema documents exactly those
fields, with `source_ip` deliberately left optional — this is the schema's
one built-in "trap" that a naive generator (manual or direct-LLM) might walk
into and a schema-aware one should not.

**Schema → IR.** The IR instance requires only `account_id`, `event_type`,
`timestamp` — all three marked `required` in the schema — so Stage 3 marks
`validation.status: "supported"`. Every field, predicate, and threshold in
the IR carries a `provenance` entry: either a literal span in the report
text, or an explicit `analyst-assumption` record for the two numbers the
narrative left implicit (5 failures, 2-minute window).

**IR → Rule.** The golden Scala file implements exactly this IR: a sliding
2-minute window per `account_id`, counting `login_failure` events, firing
when a `login_success` lands with `recent_failures >= 5`. It never
references `source_ip`, matching the IR's `excludedFields` entry.

**Rule → Evaluation.** The replay set has six scenarios, each isolating one
thing the rule must get right:

| Scenario | Account | Shape | Expected |
|---|---|---|---|
| A | `svc-backup01` | 5 failures in 95s, then success (the report's own case) | **alert** |
| B | `jdoe` | 2 failures, then success | no alert — below threshold |
| C | `attacker-probe` | 6 failures, never succeeds | no alert — sequence incomplete |
| D | `msmith` | success only | no alert — no failures |
| E | `svc-web02` | 5 failures over 10 min, then success | no alert — outside the 2-minute window |
| F | `svc-db03` | 5 failures in 60s, then success | **alert** — proves it generalises past `svc-backup01` |

Scenario E is the one that matters most: it's the case a threshold-only
implementation (count failures, ignore timing) would get wrong. Any future
NLP-extracted or LLM-generated rule that passes A and F but fails E has a
real, measurable bug — which is exactly the point of building this fixture
before the automated components exist.

## How later phases use this

- **Phase 2 (NLP):** the extractor's output on SF-SAMPLE-001 should converge
  toward this hand-written IR — this is the first data point for extraction
  F1, not a held-out test report.
- **Phase 3 (Validate):** the observability checker should reach the same
  `supported` verdict, for the same reason (required fields all present,
  `source_ip` correctly excluded rather than flagged missing).
- **Phase 4 (Compile):** the real compiler's output for this IR should be
  structurally equivalent to the golden Scala file.
- **Phase 5 (Evaluate):** this replay set is the first row in the 4-way
  benchmark table — manual / direct-LLM / schema-constrained / SENTINEL
  Forge all get run against these same six scenarios first, before the
  larger held-out set.
