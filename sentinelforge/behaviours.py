"""The one registry of supported detection behaviours.

Every other module (validator, compiler bridge, reference engine, dashboard, Sigma export) reads
from here, so a behaviour's name, recipe shape, count semantics and field dependencies cannot
drift apart between components - which is exactly how the earlier scattered tables let a
mislabelled threshold key compile silently.

Naming rule: a behaviour is named for **what its rule checks**, not for the attacker story a
report tells around it. E.g. the rule for ``multi-host-authentication`` counts distinct source
hosts among successful logins in a window; it does not observe session start/end, so it can
not claim to measure *concurrent sessions*.
"""
from __future__ import annotations

from dataclasses import dataclass, field

EVENT_COUNT = "event_count"
DISTINCT_ACCOUNTS = "distinct_accounts"
DISTINCT_HOSTS = "distinct_hosts"
COUNT_SEMANTICS = (EVENT_COUNT, DISTINCT_ACCOUNTS, DISTINCT_HOSTS)

SEQUENCE_THEN_TRIGGER = "SequenceThenTrigger"
DISTINCT_COUNT_WITHIN_WINDOW = "DistinctCountWithinWindow"
POLICY_COMPARE = "PolicyCompare"


@dataclass(frozen=True)
class Behaviour:
    id: str
    display_name: str
    checks: str                      # one sentence: exactly what the compiled rule evaluates
    recipe: str
    count_semantics: str | None      # None for policy recipes (no numeric threshold)
    windowed: bool
    attack: tuple = ()
    limitations: tuple = ()
    legacy_ids: tuple = ()
    # recipe template (never derived from a report)
    grouping_key: str | None = None
    count_event_type: str | None = None
    trigger_event_type: str | None = None
    distinct_field: str | None = None
    filter_event_type: str | None = None
    log_field: str | None = None
    policy_field: str | None = None
    comparison_op: str | None = None
    # Legacy threshold key names this behaviour's extractors ever emitted, and what each one *means*.
    # An alias that could mean two different things (e.g. "loginCount") is deliberately absent.
    threshold_aliases: dict = field(default_factory=dict)

    @property
    def log_fields(self) -> tuple:
        """Log columns the compiled rule reads or emits - knowable in advance because the recipe is fixed."""
        fields = ["event_id", "timestamp", "event_type"]
        if self.recipe == SEQUENCE_THEN_TRIGGER:
            fields += [self.grouping_key]
        elif self.recipe == DISTINCT_COUNT_WITHIN_WINDOW:
            fields += [self.grouping_key, self.distinct_field]
        elif self.recipe == POLICY_COMPARE:
            fields += ["account_id", self.log_field]
        return tuple(dict.fromkeys(fields))

    @property
    def policy_fields(self) -> tuple:
        return (f"policy.{self.policy_field}",) if self.policy_field else ()


BEHAVIOURS: dict[str, Behaviour] = {b.id: b for b in [
    Behaviour(
        id="repeated-failed-login-then-success",
        display_name="Repeated failed logins, then success",
        checks="An account has at least N login_failure events within W seconds before a login_success on the same account.",
        recipe=SEQUENCE_THEN_TRIGGER, count_semantics=EVENT_COUNT, windowed=True,
        attack=("T1110", "T1078"),
        limitations=("Counts failure events per account; does not distinguish source hosts.",
                     "The window is measured back from the success event and is closed on both ends."),
        grouping_key="account_id", count_event_type="login_failure", trigger_event_type="login_success",
        threshold_aliases={"failureCount": EVENT_COUNT},
    ),
    Behaviour(
        id="password-spray-across-accounts",
        display_name="Failed logins across many accounts from one host",
        checks="One source_host has login_failure events against at least N distinct account_ids within W seconds.",
        recipe=DISTINCT_COUNT_WITHIN_WINDOW, count_semantics=DISTINCT_ACCOUNTS, windowed=True,
        attack=("T1110.003",),
        limitations=("Counts distinct accounts, not attempts per account, so it flags the spray pattern only.",
                     "Hosts behind NAT or a shared proxy aggregate unrelated users."),
        grouping_key="source_host", distinct_field="account_id", filter_event_type="login_failure",
        threshold_aliases={"distinctAccountCount": DISTINCT_ACCOUNTS},
    ),
    Behaviour(
        id="multi-host-authentication",
        display_name="Multi-host authentication",
        checks="One account has login_success events from at least N distinct source_hosts within W seconds.",
        recipe=DISTINCT_COUNT_WITHIN_WINDOW, count_semantics=DISTINCT_HOSTS, windowed=True,
        attack=("T1078",),
        limitations=("Session overlap is NOT measured: the log has no logout/session-end event, so two "
                     "successful logins minutes apart count even if the first session had already ended.",
                     "VPN or NAT egress changes can look like multiple hosts."),
        legacy_ids=("concurrent-sessions-different-hosts",),
        grouping_key="account_id", distinct_field="source_host", filter_event_type="login_success",
        threshold_aliases={"successCount": DISTINCT_HOSTS, "distinctHostCount": DISTINCT_HOSTS},
    ),
    Behaviour(
        id="auth-method-policy-violation",
        display_name="Authentication method differs from policy",
        checks="A login_success whose auth_method differs from the account's policy.expected_auth_method.",
        recipe=POLICY_COMPARE, count_semantics=None, windowed=False,
        attack=("T1078",),
        limitations=("Compares against the recorded expected method only; it does not inspect whether the "
                     "account is a service account or whether the session was interactive.",
                     "Any mismatch fires; direction-specific policies (e.g. only password-when-certificate) "
                     "are not supported."),
        legacy_ids=("service-account-interactive-auth",),
        filter_event_type="login_success", log_field="auth_method", policy_field="expected_auth_method",
        comparison_op="notEqual",
    ),
    Behaviour(
        id="mfa-missing-on-required-account",
        display_name="MFA not used on an MFA-required account",
        checks="A login_success with mfa_used = false for an account whose policy.mfa_required is true.",
        recipe=POLICY_COMPARE, count_semantics=None, windowed=False,
        attack=("T1621", "T1556"),
        limitations=("Detects a missing second factor, not the technique used to avoid it; a "
                     "misconfigured MFA rollout produces the same signal as a real bypass.",),
        legacy_ids=("mfa-bypass-on-required-account",),
        filter_event_type="login_success", log_field="mfa_used", policy_field="mfa_required",
        comparison_op="falseWhenRequired",
    ),
]}

BEHAVIOUR_IDS = list(BEHAVIOURS)
LEGACY_IDS = {legacy: b.id for b in BEHAVIOURS.values() for legacy in b.legacy_ids}


def canonical_id(behaviour_id: str | None) -> str | None:
    """Map a legacy id (from archived results or older datasets) to its current name; pass others through."""
    return LEGACY_IDS.get(behaviour_id, behaviour_id)


def get(behaviour_id: str | None) -> Behaviour | None:
    return BEHAVIOURS.get(canonical_id(behaviour_id))
