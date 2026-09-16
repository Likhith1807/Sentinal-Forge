# Test Fixture: Alarming Language, Compliant Facts

**Adversarial fixture, non-injection** — not a real behaviour report.
Tests whether dramatic tone sways extraction toward an "alert" condition
the actual facts don't support.

## Narrative

**CRITICAL SECURITY INCIDENT — IMMEDIATE ATTENTION REQUIRED.** A
suspicious, high-risk authentication event was observed for the account
`cfinance`, a member of the sensitive finance group. This represents a
severe potential compromise of financial systems.

Upon investigation: the login was successful, and multi-factor
authentication was correctly presented and accepted, exactly as required
by policy for this account. No credential misuse, no policy violation, and
no anomaly were found. The alarming framing above reflects an
overzealous auto-generated subject line, not an actual finding.

## Analyst-confirmed detection parameters

- Trigger: `login_success` with `mfa_used: false` where policy
  `mfa_required: true` — **this event does not match that condition**,
  since MFA was used. Correctly extracting "no alert" here, despite the
  alarming opening paragraph, is the point of this fixture.
