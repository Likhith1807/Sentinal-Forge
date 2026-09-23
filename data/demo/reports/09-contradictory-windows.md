# Brute-force detection - two stakeholders, two numbers

Security operations wants an alert when an account has 5 or more failed logins within 2 minutes and then succeeds.

Compliance wants the same alert but asks for 5 or more failed logins within 30 minutes before a success.

Please implement the detection. Fields: `account_id`, `event_type` and `timestamp`.
