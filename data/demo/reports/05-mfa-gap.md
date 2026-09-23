# Sign-in without a second factor

Policy marks finance staff as MFA-required, yet the audit log shows a successful sign-in for one of them with no second factor recorded.

Alert when a successful login is recorded without MFA for an account whose policy requires MFA. The rule reads `account_id`, `event_type`, `mfa_used` and, from the policy reference, `mfa_required`.
