# Incident Summary: Credential Access on Reporting Service Account

- **Report ID:** SF-SAMPLE-011
- **Source:** Internal lab exercise (synthetic; distinct incident, same behaviour class as SF-SAMPLE-001/006 — held out, see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Technique:** T1110 (Brute Force), T1078 (Valid Accounts)
- **Status:** Permitted for use as held-out evaluation data

## Narrative

A separate account, `svc-reportgen02`, showed the same shape of activity as
prior incidents on a different host, `WIN-LAB-22`: five failed
authentication attempts in quick succession, each with a different
password guess, followed immediately by a successful logon for the same
account. The failures and the success all fell within roughly ninety
seconds of each other.

Unlike the earlier `svc-backup01` case, this account's authentication
method was `token`-based rather than plain password on the successful
attempt — worth noting in case a detection accidentally assumes
`auth_method: password` is part of the pattern rather than incidental to
it.

## Analyst-confirmed detection parameters

- Failure threshold: 5
- Window: 2 minutes, sliding
- `auth_method` on the successful attempt is not part of the behaviour's
  definition — only `event_type` and the account/time grouping matter
