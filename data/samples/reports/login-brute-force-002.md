# SOC Ticket: Account Lockout Pattern — svc-backup01

- **Report ID:** SF-SAMPLE-006
- **Source:** Internal lab exercise (synthetic; paraphrase of SF-SAMPLE-001, same underlying incident — see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Technique:** T1110 (Brute Force), T1078 (Valid Accounts)
- **Status:** Permitted for use as training data only (near-duplicate of SF-SAMPLE-001 — excluded from held-out)

## Narrative

Ticket opened after `svc-backup01` tripped a lockout-adjacent alert. Auth
log shows five back-to-back bad password attempts against this account,
all inside a two-minute span, immediately followed by one successful
logon — same account, same source machine. No 2FA challenge on the
successful attempt.

Reads like the account's password was guessed in a short burst rather than
a one-off typo. As before: source IP wasn't logged for this session
(known gap for internal hosts), don't build anything that needs it.

## Analyst-confirmed detection parameters

- Failure threshold: 5
- Window: 2 minutes, sliding
- Same account required on both the failures and the following success
