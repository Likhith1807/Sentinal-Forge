# Test Fixture: Deliberately Unsupported Behaviour

**Not one of the project's 5 behaviours** (see
[`docs/behaviours.md`](../../../docs/behaviours.md)) — this exists solely
to exercise the schema-constrained baseline's rejection path end-to-end in
[`experiments/baselines/schema_constrained/generate.py`](../../../experiments/baselines/schema_constrained/generate.py),
alongside the unit-level check already in `evaluate_stage3.py`.

## Narrative

A successful login was recorded for account `svc-api04` from a country
that account has never authenticated from before, based on IP geolocation.
This is a classic impossible-travel indicator.

## Analyst-confirmed detection parameters

- Trigger: `login_success` where the source IP's geolocated country is not
  in the account's historical set of countries
- Requires: `account_id`, `event_type`, geolocated country of `source_ip`

This cannot be supported by `authentication_log_schema.json` v1:
`source_ip` is itself annotated `observability: unreliable`, and no
geolocation field exists in the schema at all.
