# SOC Ticket: Dual-Host Session for rjones

- **Report ID:** SF-SAMPLE-008
- **Source:** Internal lab exercise (synthetic; paraphrase of SF-SAMPLE-003, same underlying incident — see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Technique:** T1078 (Valid Accounts)
- **Status:** Permitted for use as training data only (near-duplicate of SF-SAMPLE-003 — excluded from held-out)

## Narrative

`rjones` shows two clean, MFA-satisfied logons four minutes apart — one
from workstation `WKS-330`, one from an external gateway `EXT-GW-04`.
Neither login looks bad in isolation; flagged because of the host
mismatch and tight timing, not because either credential check failed.

## Analyst-confirmed detection parameters

- Window: 15 minutes, sliding, per account
- Trigger: 2+ successful logins from different hosts inside the window
- No failed attempts required beforehand
