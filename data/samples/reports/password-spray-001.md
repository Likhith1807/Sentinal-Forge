# Incident Summary: Password Spray Against Multiple Accounts

- **Report ID:** SF-SAMPLE-002
- **Source:** Internal lab exercise (synthetic, authored for SENTINEL Forge development and evaluation)
- **ATT&CK Technique:** T1110.003 (Password Spraying)
- **Status:** Permitted for use as training / evaluation data

## Narrative

Analysts reviewing VPN authentication activity identified a pattern
inconsistent with normal user error: within a ten-minute window, the
gateway `EXT-VPN-03` recorded failed authentication attempts against five
distinct accounts — `alice`, `bob`, `carol`, `dave`, and `erin` — with each
account attempted only once. No account received more than one failure.

This is the signature of password spraying rather than brute force: instead
of exhausting many passwords against one account (which would trip
per-account lockout policies), the attacker tries one or two common
passwords across many accounts, staying under any single account's failure
threshold while still covering a large amount of credential-guessing
surface. A detection built around "many failures for one account" — such as
the one described in SF-SAMPLE-001 — will not catch this; the grouping key
has to be the source, not the account.

## Analyst-confirmed detection parameters

- **Distinct-account threshold:** failed attempts touching 4 or more
  distinct accounts
- **Time window:** 10 minutes, sliding, per source host
- **Per-account cap:** the pattern holds regardless of whether any
  individual account also succeeds afterward — that is a separate,
  already-covered behaviour (SF-SAMPLE-001), not a requirement of this one
