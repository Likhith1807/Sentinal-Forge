# Account Policy Reference (v1)

This is deliberately **not** part of the authentication log schema. It's a
small, slow-changing identity/policy table — the kind of context a real
detection stack joins event data against (an asset inventory, an IAM export)
rather than something a log collector emits per event. Keeping it separate
is itself a modelling decision Stage 3 has to reason about: a behaviour that
needs `expected_auth_method` or `mfa_required` is only "supported" if
*both* the event schema and this reference table cover it — and if an
`account_id` used in a rule doesn't appear in this table at all, that's a
distinct, more specific rejection than "field missing from the log schema."

Machine-readable version:
[`data/samples/schema/account_policy_reference.json`](../../data/samples/schema/account_policy_reference.json).

## Fields

| Field                 | Type    | Notes |
|------------------------|---------|-------|
| `account_id`           | string  | Joins to `authentication_log.account_id`. |
| `account_type`         | enum    | `human` \| `service`. |
| `expected_auth_method` | enum    | One of `password`, `token`, `certificate`. What this account *should* authenticate with. |
| `mfa_required`         | boolean | Whether policy mandates MFA for this account. |

## Coverage

The reference table is intentionally incomplete — three accounts used in
the replay data have no entry at all, so behaviours B4/B5 have a real
"account not in policy table" case to reject on, not just a hypothetical
one.
