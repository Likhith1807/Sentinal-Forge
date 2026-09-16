# SOC Ticket: Distributed Login Failures From VPN Gateway

- **Report ID:** SF-SAMPLE-007
- **Source:** Internal lab exercise (synthetic; paraphrase of SF-SAMPLE-002, same underlying incident — see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Technique:** T1110.003 (Password Spraying)
- **Status:** Permitted for use as training data only (near-duplicate of SF-SAMPLE-002 — excluded from held-out)

## Narrative

Gateway `EXT-VPN-03` logged single bad-password attempts against five
separate accounts (`alice`, `bob`, `carol`, `dave`, `erin`), all inside a
ten-minute stretch, one attempt per account. No account was hit more than
once.

Classic spray shape — spread thin across accounts instead of piling up on
one, so nothing crosses a per-account lockout counter. Grouping key here
has to be the source host, not any single account.

## Analyst-confirmed detection parameters

- Distinct-account threshold: 4 or more
- Window: 10 minutes, sliding, per source host
