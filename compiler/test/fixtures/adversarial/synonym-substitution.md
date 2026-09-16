# Test Fixture: Non-Standard Terminology for a Known Behaviour

**Adversarial fixture, non-injection** — not a real behaviour report.
Describes the same underlying behaviour as SF-SAMPLE-001
(repeated-failed-login-then-success), but avoids the vocabulary that
report uses throughout, to test whether extraction depends on specific
wording rather than the actual pattern. Classical extraction is
keyword/pattern-based by design (`nlp/src/classical_extractor.py`); this
is the fixture built specifically to find where that breaks.

## Narrative

The identity `svc-backup01` experienced five consecutive credential
rejections, each attempt using a different guessed passphrase, occurring
in rapid succession over roughly ninety ticks of the clock. Immediately
afterward, a session was successfully established for that same identity.

## Analyst-confirmed detection parameters

- Rejection count needed: 5
- Time span: 120 ticks (seconds), sliding
- Same identity required across the rejected attempts and the
  subsequently established session
