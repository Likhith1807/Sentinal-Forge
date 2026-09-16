# Incident Summary: Concurrent Sessions From Two Hosts

- **Report ID:** SF-SAMPLE-003
- **Source:** Internal lab exercise (synthetic, authored for SENTINEL Forge development and evaluation)
- **ATT&CK Technique:** T1078 (Valid Accounts)
- **Status:** Permitted for use as training / evaluation data

## Narrative

While reconciling session activity, analysts found that the account
`rjones` authenticated successfully from workstation `WKS-330`, and then
authenticated successfully again from a second, unrelated host
`EXT-GW-04`, only four minutes later. Both sessions used correct
credentials and, in this case, MFA was satisfied on both — this is not a
credential-guessing pattern, and neither individual login looks anomalous
on its own.

What makes this worth flagging is the combination: a single human account
does not normally hold active sessions from two different hosts, one of
them external, within minutes of each other. Plausible explanations range
from a shared or stolen session token being reused elsewhere, to a
legitimate but unusual working pattern (e.g. a VPN client and a personal
device both retrying a stale credential). The report does not attempt to
resolve which explanation applies — that judgment belongs to the analyst
reviewing the alert, not the detection.

## Analyst-confirmed detection parameters

- **Time window:** 15 minutes, sliding, per account
- **Trigger:** 2 or more `login_success` events for the same account from
  **different** `source_host` values inside the window
- **Not required:** any failed attempts beforehand — this behaviour can and
  should fire on two entirely successful logins
