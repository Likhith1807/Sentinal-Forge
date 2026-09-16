# Authentication Log Schema (v1)

This is the one documented security-log schema for SENTINEL Forge's initial
scope. It describes the fields available from the lab environment's
authentication event collector. Stage 3 (Validate the Specification) checks
every extracted behaviour against exactly this schema — a condition that
needs a field not listed here is unsupported and must be rejected, not
guessed around.

The machine-readable version compiler and NLP components read from is
[`authentication_log_schema.json`](../../data/samples/schema/authentication_log_schema.json).

## Fields

| Field          | Type      | Required | Notes |
|----------------|-----------|----------|-------|
| `event_id`     | string    | yes      | Unique per event, collector-assigned. |
| `timestamp`    | ISO-8601  | yes      | Event time, UTC, millisecond precision. |
| `account_id`   | string    | yes      | Username or service-account identifier. Grouping key for most behaviours. |
| `event_type`   | enum      | yes      | One of `login_success`, `login_failure`. |
| `source_host`  | string    | yes      | Hostname or IP of the machine the auth request originated from. |
| `source_ip`    | string    | **no**   | Frequently dropped for internal-network sessions by this collector — see report SF-SAMPLE-001. Treat as corroborating, never a required predicate field. |
| `auth_method`  | enum      | yes      | One of `password`, `token`, `certificate`. |
| `mfa_used`     | boolean   | yes      | Whether a second factor was presented and accepted. |
| `session_id`   | string    | no       | Present only on `login_success` events. |

## Why `source_ip` is marked optional

This is the schema's one deliberately "leaky" field, included on purpose so
Stage 3 has a real, documented case to reason about: a field that a naive
rule-generator (manual or direct-LLM) might casually include because reports
often mention source IPs, but that this specific log schema cannot reliably
provide. A rule that hard-requires `source_ip` should be flagged as
unsupported by this schema, not silently compiled.

## Coverage for the initial five behaviours

| Behaviour                                              | Needs fields beyond the above? |
|---------------------------------------------------------|-------------------------------|
| Repeated failed logins → success (this example)          | No — fully observable |
| *(remaining four behaviours to be documented as they are added in Phase 1)* | — |
