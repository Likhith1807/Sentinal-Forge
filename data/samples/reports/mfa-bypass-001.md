# Incident Summary: Successful Login Without Required MFA

- **Report ID:** SF-SAMPLE-005
- **Source:** Internal lab exercise (synthetic, authored for SENTINEL Forge development and evaluation)
- **ATT&CK Techniques:** T1621 (Multi-Factor Authentication Request Generation), T1556 (Modify Authentication Process)
- **Status:** Permitted for use as training / evaluation data

## Narrative

Organisation policy requires MFA for all human accounts in the finance
group. The authentication log recorded a `login_success` event for the
account `cfinance` with `mfa_used: false`.

Whether this reflects an MFA-fatigue attack (the user or an attacker
approving a push prompt to make it stop, then the session showing as
unchallenged on a subsequent silent re-auth), a misconfigured application
that skips the MFA step, or a stolen session cookie replayed without
re-challenging, this event should never occur for an account policy marks
as MFA-required, and is worth an analyst's attention regardless of cause.

As with SF-SAMPLE-004, whether MFA was *required* for this account is not
information the authentication log carries — it comes from the same
account policy reference. A related, narrower case worth noting for the
compiler: if an account appears in log events but has **no** entry in the
policy reference at all, the system has no basis to know whether MFA was
required, and must not guess in either direction.

## Analyst-confirmed detection parameters

- **Trigger:** `login_success` with `mfa_used: false` for an account whose
  policy reference entry has `mfa_required: true`
- **Explicitly out of scope:** accounts absent from the policy reference —
  these must be rejected as unsupported for this specific behaviour, not
  treated as either compliant or violating
