# Incident Summary: Credential Guessing Across Marketing Team Accounts

- **Report ID:** SF-SAMPLE-012
- **Source:** Internal lab exercise (synthetic; distinct incident, same behaviour class as SF-SAMPLE-002/007 — held out, see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Technique:** T1110.003 (Password Spraying)
- **Status:** Permitted for use as held-out evaluation data

## Narrative

A different external gateway, `EXT-VPN-07`, recorded failed sign-in
attempts against six marketing-team accounts (`ktan`, `mrivera`, `swong`,
`jbrooks`, `dlim`, `ohassan`) over about twelve minutes. Each account was
tried once; no account had more than a single failure, and none of the six
went on to a successful login during the period reviewed.

Analysts noted the six-account, twelve-minute shape is wider and slower
than the previous VPN-03 case, but still clears the same distinct-account
threshold — the detection should not be sensitive to the exact account
count or window length beyond the stated minimums.

## Analyst-confirmed detection parameters

- Distinct-account threshold: 4 or more (this incident: 6)
- Window: 10 minutes, sliding, per source host (this incident's span: ~12
  minutes overall, but the qualifying 4th-account crossing still occurs
  inside a 10-minute sliding window — analysts confirmed the rule should
  key off the sliding window, not the incident's total observed duration)
