# Incident Summary: MFA-Required Account Authenticated Without Challenge

- **Report ID:** SF-SAMPLE-015
- **Source:** Internal lab exercise (synthetic; distinct incident, same behaviour class as SF-SAMPLE-005/010 — held out, see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Techniques:** T1621, T1556
- **Status:** Permitted for use as held-out evaluation data

## Narrative

The account `dpatel`, part of the legal team's MFA-required group, shows a
successful login with `mfa_used: false` from an unfamiliar workstation,
`WKS-905`. Unlike SF-SAMPLE-005/010, `auth_method` here was `token`, not
`password` — included so a detection can't accidentally key off the
authentication method instead of the `mfa_used` flag itself.

## Analyst-confirmed detection parameters

- Trigger: `login_success` with `mfa_used: false` where policy `mfa_required: true`
- `auth_method` is not part of this behaviour's definition
- Accounts absent from the policy reference: unsupported, not a silent pass
