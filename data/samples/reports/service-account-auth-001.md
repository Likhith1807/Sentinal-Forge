# Incident Summary: Service Account Authenticating Interactively

- **Report ID:** SF-SAMPLE-004
- **Source:** Internal lab exercise (synthetic, authored for SENTINEL Forge development and evaluation)
- **ATT&CK Technique:** T1078.003 (Valid Accounts: Local Accounts)
- **Status:** Permitted for use as training / evaluation data

## Narrative

The service account `svc-etl05` is provisioned to authenticate exclusively
via certificate — it drives a scheduled ETL job and has no legitimate
reason to type a password. Authentication logs recorded a successful login
for `svc-etl05` using `auth_method: password` instead.

A single password-based login for a certificate-only service account is a
strong indicator of credential misuse: either the account's password
(which should not be in active use, but often still exists for legacy
reasons) has been obtained and used directly, or someone is running the
job manually outside its intended automation. This is not observable from
the authentication log alone — the log only records which method was
*used*; knowing which method was *expected* requires the account's
provisioning policy, which analysts pulled separately from the identity
system.

## Analyst-confirmed detection parameters

- **Trigger:** any `login_success` event where `auth_method` does not
  match the account's `expected_auth_method` in the policy reference
- **Scope:** applies only to accounts present in the policy reference —
  an account with no policy entry cannot be evaluated against this
  behaviour and must not be silently assumed compliant
