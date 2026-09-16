# The Five Observable Behaviours (v1)

Per the project's initial scope, every behaviour below must be fully
observable from the one documented log schema
([`authentication-log-schema.md`](schema/authentication-log-schema.md)),
optionally joined against one small static reference table
([`account-policy-reference.md`](schema/account-policy-reference.md)) that
carries identity/policy context rather than events. Adding that reference
table is a deliberate, documented choice — real detection engineering
routinely joins event logs against asset/identity context, and modelling
that honestly is more representative than pretending every signal lives in
one event stream.

| ID | Behaviour | ATT&CK | Report | Needs policy reference? |
|----|-----------|--------|--------|--------------------------|
| B1 | Repeated failed logins, then a success, for one account | T1110 (Brute Force), T1078 (Valid Accounts) | [`login-brute-force-001.md`](../data/samples/reports/login-brute-force-001.md) | No |
| B2 | Low-and-slow attempts against many distinct accounts from one source | T1110.003 (Password Spraying) | [`password-spray-001.md`](../data/samples/reports/password-spray-001.md) | No |
| B3 | Successful sessions for the same account from two hosts, close together in time | T1078 (Valid Accounts) | [`concurrent-sessions-001.md`](../data/samples/reports/concurrent-sessions-001.md) | No |
| B4 | A service account authenticating with an interactive method it isn't provisioned for | T1078.003 (Valid Accounts: Local Accounts) | [`service-account-auth-001.md`](../data/samples/reports/service-account-auth-001.md) | Yes — `expected_auth_method` |
| B5 | A successful login without MFA on an account policy marks as MFA-required | T1556, T1621 (Modify/Multi-Factor Authentication Interception) | [`mfa-bypass-001.md`](../data/samples/reports/mfa-bypass-001.md) | Yes — `mfa_required` |

B1 is the Phase 0 canonical example, already carried through report → IR →
compiled rule → replay set. B2–B5 have the same "report + manual baseline +
labelled replay" depth as of Phase 1; they get their full IR and
golden-compiler treatment in Phase 3–4, alongside B1.

Each behaviour now has 3 reports (the original above, a paraphrase, and a
held-out variant) — see [`docs/data-splits.md`](data-splits.md) for the
full list and, importantly, *why* the paraphrase and the held-out report
are not interchangeable.

## Why these five, and not others

Each of B2–B5 stresses a different part of the pipeline, on purpose:

- **B2** needs a *different* grouping key shape — grouped by `source_host`
  across many `account_id` values, not one account across many events. A
  compiler or extractor that hardcodes "group by account" breaks here.
- **B3** needs a same-account, cross-entity correlation (two different
  `source_host` values) rather than a simple threshold — closer to a join
  than a count.
- **B4** and **B5** need the account-policy reference table, which means
  Stage 3's observability check has to reason about *two* data sources, and
  correctly reject a spec that references a policy field for an account not
  present in the reference table — a realistic "partial observability" case
  neither B1–B3 exercise.
