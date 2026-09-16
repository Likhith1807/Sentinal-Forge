"""The controlled vocabulary both extractors are constrained to.

Both the classical and transformer-based extractors in this package output
field names from this exact list — never free text. This mirrors what
Stage 3 (Validate the Specification) will require in Phase 3: a behaviour
spec can only be checked against a log schema if it's expressed in that
schema's own field names. Scoring extraction accuracy on free-text field
names would also be close to meaningless (is "the account" the same
answer as "account_id"?), so fixing the vocabulary up front is both more
realistic and the only way to get a well-defined F1 number.
"""

LOG_FIELDS = [
    "event_id", "timestamp", "account_id", "event_type",
    "source_host", "source_ip", "auth_method", "mfa_used", "session_id",
]

POLICY_FIELDS = [
    "policy.expected_auth_method",
    "policy.mfa_required",
]

ALL_FIELDS = LOG_FIELDS + POLICY_FIELDS

# The 5 behaviours in docs/behaviours.md. Constraining behaviourId to this
# list too (not just the field names) matters for the same reason: without
# it, a semantically-correct but differently-worded id (e.g.
# "simultaneous-access-different-hosts" for "concurrent-sessions-different-hosts")
# would score as wrong under exact-string matching, which tests vocabulary
# guessing, not extraction quality.
BEHAVIOUR_IDS = [
    "repeated-failed-login-then-success",
    "password-spray-across-accounts",
    "concurrent-sessions-different-hosts",
    "service-account-interactive-auth",
    "mfa-bypass-on-required-account",
]
