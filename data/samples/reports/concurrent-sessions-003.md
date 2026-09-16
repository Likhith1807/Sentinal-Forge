# Incident Summary: Simultaneous Access From Two Locations — Finance Contractor

- **Report ID:** SF-SAMPLE-013
- **Source:** Internal lab exercise (synthetic; distinct incident, same behaviour class as SF-SAMPLE-003/008 — held out, see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Technique:** T1078 (Valid Accounts)
- **Status:** Permitted for use as held-out evaluation data

## Narrative

The account `nokafor` authenticated successfully from `WKS-812`, then
successfully again nine minutes later from an unrelated host,
`EXT-GW-11`. Both logins passed MFA. No failed attempts preceded either
login on this account that day.

Analysts flagged the nine-minute gap specifically because it's close to
half the confirmed window — useful for checking a detection doesn't only
work on the tightest possible timing from the original example.

## Analyst-confirmed detection parameters

- Window: 15 minutes, sliding, per account (this incident: 9-minute gap,
  well inside the window but not as tight as SF-SAMPLE-003's 4 minutes)
- Trigger: 2+ successful logins from different hosts inside the window
