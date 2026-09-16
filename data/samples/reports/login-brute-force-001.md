# Incident Summary: Credential Access via Repeated Authentication Attempts

- **Report ID:** SF-SAMPLE-001
- **Source:** Internal lab exercise (synthetic, authored for SENTINEL Forge development and evaluation; not derived from any real incident or organisation)
- **ATT&CK Techniques:** T1110 (Brute Force), T1078 (Valid Accounts)
- **Status:** Permitted for use as training / evaluation data

## Narrative

During a routine review of authentication activity, analysts observed a pattern
consistent with a brute-force credential access attempt. For the account
`svc-backup01`, the authentication log recorded five consecutive failed
sign-in attempts within a two-minute window, each attempt using a different
password guess against the same username. Immediately following the fifth
failure, a sixth authentication event for the same account succeeded from the
same source host. No multi-factor challenge was recorded on the successful
attempt.

This sequence — repeated failures immediately followed by a success for the
same account — is a strong indicator that the attacker exhausted a password
list and gained access, rather than a legitimate user mistyping a password
once or twice. A single failed attempt followed by a success, or failures
spread across several hours, should not be treated the same way.

Analysts also noted that the source IP address field is not consistently
populated in this environment's authentication log — some collectors drop it
for internal-network sessions. Any detection built from this report should
treat `source_ip` as corroborating evidence only, not as a required field.

## Analyst-confirmed detection parameters

These were not stated numerically in the original narrative and were
confirmed with the reporting analyst before compilation, per SENTINEL
Forge's requirement that unspecified thresholds be made explicit rather than
guessed by the model:

- **Failure threshold:** 5 failed attempts
- **Time window:** 2 minutes, sliding, per account
- **Required immediately after:** 1 successful authentication for the same
  account within the same window
