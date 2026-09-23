# Runbook: fast brute-force then success

Watch for an account that gets **6 or more failed logins within 90 seconds** and then logs in successfully. Slower guessing (the same failures spread over 90 minutes) is a different problem and is handled by the lockout policy; do not alert on it here.

Required log fields: `account_id`, `event_type`, `timestamp`.
