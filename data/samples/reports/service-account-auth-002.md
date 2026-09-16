# SOC Ticket: svc-etl05 Password Auth

- **Report ID:** SF-SAMPLE-009
- **Source:** Internal lab exercise (synthetic; paraphrase of SF-SAMPLE-004, same underlying incident — see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Technique:** T1078.003 (Valid Accounts: Local Accounts)
- **Status:** Permitted for use as training data only (near-duplicate of SF-SAMPLE-004 — excluded from held-out)

## Narrative

`svc-etl05` is certificate-only per provisioning records. Logs show a
successful login for this account using `auth_method: password` instead.
Either the legacy password is still active and got used, or someone ran
the ETL job by hand outside automation. Can't tell which from the log
alone — that's for the analyst reviewing the alert, not the detection.

## Analyst-confirmed detection parameters

- Trigger: `login_success` where `auth_method` != `expected_auth_method` from policy
- Scope: only accounts present in the policy reference
