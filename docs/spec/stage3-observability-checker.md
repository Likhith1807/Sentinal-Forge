> **Historical document.** Written during an earlier phase of the project and kept as a record. Numbers here were measured on the original 44-report set and the pre-audit pipeline; the current, audited results and limits are in [`docs/evaluation.md`](../evaluation.md) and [`docs/limitations.md`](../limitations.md).

# Stage 3 — Validate the Specification

> **Corrected 2026-09-16** — an independent review found this checker
> accepted an empty spec, an arbitrary unknown `behaviourId`, and
> negative/zero threshold and time-window values, all as "supported." All
> 3 reproduced and fixed; see
> [`docs/spec/independent-review-corrections.md`](independent-review-corrections.md).
> The document below was written before that correction and describes the
> checker's design; the design is still accurate, the earlier
> implementation of it was incomplete.

`compiler/src/observability_checker.py` takes a spec's `requiredFields` and
`policyFields` (the shape Phase 2's extractors produce — see
[`nlp/src/schema_fields.py`](../../nlp/src/schema_fields.py)) and checks
each one against the real schema files:
[`authentication_log_schema.json`](../../data/samples/schema/authentication_log_schema.json)
and
[`account_policy_reference.json`](../../data/samples/schema/account_policy_reference.json).
A field is rejected if it isn't in the schema at all, or if it's annotated
`observability: "unreliable"` — currently only `source_ip`. Every rejection
carries the specific field and reason, never a generic "spec invalid."

**Prototype language note:** written in Python, not the Scala the README
specifies for the compiler. This environment has Java 8 but no Scala/sbt
installed — checked directly (`java -version` succeeds, `scala`/`sbt` do
not), not assumed. Installing a full Scala toolchain is a heavier action
than fits this pass; it's an open item for Phase 4, when the compiler needs
to actually emit and run Spark code, not silently skipped. The validation
logic is the real contribution here, independent of the language it's
written in.

## Verified against 17 real cases

`compiler/test/evaluate_stage3.py` runs the checker against:
1. A deliberately bad spec requiring `source_ip` — **correctly rejected**,
   with the specific reason quoted from the schema annotation.
2. The Phase 0 canonical IR and all 5 held-out gold specs — **all
   correctly supported** (they're hand-authored to need only observable
   fields, so this checks the checker doesn't have false positives).
3. **The live output of both Phase 2 extractors**, run for real on the 5
   held-out reports and piped straight into the checker — 10 more cases,
   all correctly supported.

All 17 passed. Full output in
[`experiments/results/phase3_validation_eval.json`](../../experiments/results/phase3_validation_eval.json).

## The result that matters most: what Stage 3 does *not* catch

Case 3 includes the classical extractor's misclassification from Phase 2:
for SF-SAMPLE-011 (a repeated-failed-login-then-success report), the
classical extractor guessed `behaviourId: multi-host-authentication`
— genuinely the wrong behaviour — but its `requiredFields`
(`account_id`, `event_type`, `timestamp`) are all real, observable fields.
**Stage 3 approved it.**

This is not a bug to fix; it's the actual boundary of what an
observability check can do. Stage 3 answers one question — *can the named
fields be read from this schema?* — and nothing else. It cannot tell you:

- that the wrong behaviour was identified,
- that a field the behaviour actually needs was never requested (silence
  looks identical to correctness from Stage 3's point of view), or
- that the thresholds or time window are wrong.

Those require actually running the compiled rule against independently
labelled events and checking the alerts — which is exactly why Phase 5's
replay evaluation exists as a separate stage, not a superset of what
Stage 3 already covers. Stage 3's job is narrower and cheaper (catch
unsupported specs before ever compiling them); Phase 5's job is to catch
everything Stage 3 structurally cannot.
