# Direct-LLM-Generation Baseline — Run Log

**Status: executed.** Provider: Groq, model `openai/gpt-oss-120b` (see each
`generated/*.meta.json` for the exact call). Prompt: `prompt_template.md`,
unmodified. Output: `generated/*.scala`, unedited except for stripping a
markdown code fence the model wrapped its answer in (`strip_code_fence` in
`generate.py`) — no logic was touched.

This is a real run, not a simulated one. Nothing below was written before
reading the actual generated code.

## Findings from this run

**1. Both policy-dependent rules silently drop unknown accounts (confirmed).**
`service-account-auth-001.scala` and `mfa-bypass-001.scala` each join the
event stream against the account-policy reference with an **inner join**.
The MFA one says so directly in its own doc comment: *"Accounts absent from
the policy reference are ignored (no alert generated)."*

This means both rules collapse two outcomes the manual baseline keeps
distinct — `no_alert` (checked, compliant) and `insufficient_context`
(couldn't check, no policy entry) — into one silent non-alert. Against
`service_account_auth_labels.json`'s `SA-NEG-NO-POLICY` scenario and
`mfa_bypass_labels.json`'s `MFA-NEG-NO-POLICY` scenario, this produces the
*correct* surface behaviour (no alert) for the *wrong* reason (the row was
dropped by the join, not evaluated and found fine) — which is exactly why
`insufficient_context` needs to be measured as its own category in Phase 5
rather than folded into a plain true/false accuracy number: a metric that
only checks "did it alert or not" would never catch this, even though it's
a real difference in what the system actually knows.

**2. Neither policy-dependent rule was told the reference table exists (as designed).**
The prompt only ever supplied the authentication log schema — see
`prompt_template.md`. Both generated rules *assume* a table named
`account_policy` is available in the Spark catalog, which is a reasonable
guess but was never confirmed against anything. This is the direct
comparison point for SENTINEL Forge's Stage 3, which would reject a spec
referencing `account_policy` fields until that source is confirmed
observable, rather than silently assuming a catalog table exists.

**3. Preliminary, not yet measured: likely duplicate alerting on B2/B3.**
`password-spray-001.scala` and `concurrent-sessions-001.scala` both use
Spark's `window(..., "10 minutes", "1 minute")` sliding-window aggregation,
which emits one row per overlapping 1-minute-shifted window rather than one
row per incident. Run against real data this would likely produce several
duplicate alerts per true positive (the manual baseline's equivalent rules
collapse to one row per incident via a final `groupBy`). This is flagged as
a hypothesis for Phase 5's replay harness to confirm quantitatively, not
claimed as a measured result here — do not cite an alert-multiplicity
number until it's actually been run against `data/processed/events/`.

## Known integration gap

The five generated files use three different `detect` signatures (some
take just a `DataFrame`, one takes an implicit `SparkSession`, none match
`experiments/baselines/manual/`'s consistent
`detect(spark: SparkSession, events: DataFrame)`). Phase 5's harness will
need a thin adapter per system rather than assuming a shared interface —
noting this now so it isn't a surprise when the harness is built.
