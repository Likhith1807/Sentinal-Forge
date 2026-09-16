# Incident Summary: Token-Only Service Account Authenticated With Certificate

- **Report ID:** SF-SAMPLE-014
- **Source:** Internal lab exercise (synthetic; distinct incident, same behaviour class as SF-SAMPLE-004/009 — held out, see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Technique:** T1078.003 (Valid Accounts: Local Accounts)
- **Status:** Permitted for use as held-out evaluation data

## Narrative

The service account `svc-notify09` is provisioned for `token`-based
authentication only, per the identity system export. A successful login
was recorded for this account using `auth_method: certificate` — a
different mismatch direction than SF-SAMPLE-004/009 (password used where
certificate was expected), included specifically so a detection can't get
away with hardcoding "flag password auth" instead of a general
not-equal-to-expected comparison.

## Analyst-confirmed detection parameters

- Trigger: `login_success` where `auth_method` != `expected_auth_method`
  from policy — any mismatch direction, not specifically "password used
  instead of certificate"
- Scope: only accounts present in the policy reference
