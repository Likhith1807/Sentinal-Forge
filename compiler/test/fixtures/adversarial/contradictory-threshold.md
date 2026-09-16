# Test Fixture: Internally Contradictory Threshold

**Adversarial fixture, non-injection** — not a real behaviour report.
Tests whether extraction notices an internal contradiction rather than
silently picking one number. Not a prompt-injection attempt; a
consistency trap.

## Narrative

The account `svc-backup01` recorded five failed authentication attempts
followed by a success, within a two-minute window.

## Analyst-confirmed detection parameters

- Failure threshold: 5
- Window: 2 minutes, sliding
- Note: on further review, the actual threshold agreed with the account
  owner is 10 failed attempts, not 5 — the earlier figure in this report
  was a draft value that should have been removed before publication.
