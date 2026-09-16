# SOC Ticket: cfinance Login Without MFA

- **Report ID:** SF-SAMPLE-010
- **Source:** Internal lab exercise (synthetic; paraphrase of SF-SAMPLE-005, same underlying incident — see [`docs/data-splits.md`](../../../docs/data-splits.md))
- **ATT&CK Techniques:** T1621, T1556
- **Status:** Permitted for use as training data only (near-duplicate of SF-SAMPLE-005 — excluded from held-out)

## Narrative

`cfinance` is in the MFA-required finance group. Log shows a successful
login for this account with `mfa_used: false`. Could be MFA fatigue, a
skipped-MFA app bug, or a replayed session — the log doesn't say which,
and the detection shouldn't need to know.

As with the earlier case: if an account has no entry in the policy
reference at all, we have no basis for saying MFA was or wasn't required,
and the system needs to say so rather than staying silent either way.

## Analyst-confirmed detection parameters

- Trigger: `login_success` with `mfa_used: false` where policy `mfa_required: true`
- Accounts absent from the policy reference: unsupported, must not be
  silently treated as either compliant or violating
