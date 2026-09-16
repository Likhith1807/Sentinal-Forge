# Schema-Constrained Generation Baseline — Run Log

**Status: executed.** Pipeline: `nlp/src/transformer_extractor.py` (extract)
→ `compiler/src/observability_checker.py` (Stage 3 validate) → generate
*only if supported*, using *only* the validated field list
(`prompt_template.md`). Same underlying model as the direct-LLM baseline
(Groq, `openai/gpt-oss-120b`) — every difference in outcome below comes
from the validation gate, not a different or better model.

## The rejection path works end-to-end

`compiler/test/fixtures/unsupported-geo-anomaly.md` describes a behaviour
this schema cannot support (geolocation isn't a field at all, and
`source_ip` is explicitly unreliable). Running `generate.py` against it:

```
REJECTED by Stage 3 — wrote .../unsupported-geo-anomaly.rejected.json, no code generated.
  - behaviourId 'unrecognized:None' does not match any known, schema-validated behaviour pattern.
```

No Scala file was produced — a `.rejected.json` artifact was, with the
specific reason. This is the capability `experiments/baselines/direct_llm/`
structurally cannot have: nothing in that pipeline ever checks its own
output before returning it.

(This case also caught a real gap in Stage 3 itself: the checker originally
only verified that *named* fields were observable, so a spec naming only
valid fields but describing an unrecognized behaviour — as this one did —
slipped through as "supported." Fixed in `observability_checker.py` by
rejecting any `behaviourId` matching the `unrecognized:` convention
`transformer_extractor.py` already used. Left in this log rather than
edited away, because it's a genuine example of validation logic missing a
real case on the first pass.)

## The finding that matters most: constraining fields doesn't just weaken output — it can make a model fabricate a substitute

All 5 held-out reports were marked `supported`, but on this run, 2 of the
5 (`service-account-auth-003`, `mfa-bypass-003`) had their extraction miss
the policy-reference field they actually need
(`policy.expected_auth_method`, `policy.mfa_required` — see the run-to-run
extraction variance already noted in `nlp/README.md`). Since those fields
were never in the validated list handed to the generation prompt, the
model couldn't reference them. What it did instead, for
`service-account-auth-003`, is worse than simply omitting the check:

```scala
// Policy reference: accounts that are allowed token‑only authentication
private val tokenOnlyAccounts = Set(
  "svc-notify09" // add additional account IDs here as needed
)
```

It fabricated a hardcoded, single-account allowlist — lifted directly from
the one example account named in the report — as a stand-in for the real
policy join it wasn't given access to. This rule would never fire for any
other service account, ever, regardless of that account's actual policy.
The `mfa-bypass-003` case failed more simply: it just dropped the
`mfa_required` condition entirely, alerting on *every* MFA-less success
account-wide regardless of whether that account's policy requires MFA.

Neither failure was caught by Stage 3 — by design, per
`docs/spec/stage3-observability-checker.md`: Stage 3 only checks that named
fields are observable, not that all the *necessary* fields were named. Both
are real, concrete instances of exactly the abstract limitation that
document predicted, not hypothetical ones. This is precisely why Phase 5's
replay evaluation has to actually run the compiled rule against labelled
events for accounts both with and without a policy entry — a static check,
however good, cannot catch a rule that hardcodes the wrong thing when a
data source is withheld from it.
