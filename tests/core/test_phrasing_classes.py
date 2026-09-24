"""Classes of phrasing and safety failure found by the frozen holdout v1 (see experiments/results/holdout/FAILURE_ANALYSIS.md).

Every example here is NEW wording written for the class - never a holdout text - so these tests pin the generalisation,
not a memorised sentence. The safety block matters most: each of these must produce NO compiled rule.
"""
from __future__ import annotations

import pytest

from sentinelforge.pipeline import analyze

B1, B2, B3 = "repeated-failed-login-then-success", "password-spray-across-accounts", "multi-host-authentication"
B4, B5 = "auth-method-policy-violation", "mfa-missing-on-required-account"


def rule(text):
    a = analyze(text, None)
    assert a.compiled is not None, (a.status, [(r.code, r.message[:80]) for r in a.reconciliation.reasons])
    c = a.compiled
    return c["behaviourId"], c.get("countThreshold") or c.get("distinctThreshold"), c.get("timeWindowSeconds")


# ------------------------------------------------------------------------------------------------- recall
@pytest.mark.parametrize("text,expected", [
    # count forms
    ("Please raise an alert on 6x failed logins within 3min for one account followed by a successful login.", (B1, 6, 180)),
    ("Trigger when failed sign-ins for one account reach or exceed 9 inside a rolling 45s window and a successful sign-in follows.", (B1, 9, 45)),
    ("The rule fires for any account with >= 4 failed logins inside 2 minutes and then a successful login.", (B1, 4, 120)),
    ("Minimum 7 failed logins in 5 minutes for the same account, then it succeeds.", (B1, 7, 300)),
    ("Meets or exceeds 5 failed logins for a single account in 90 seconds and a successful login follows straight after.", (B1, 5, 90)),
    # number word + parenthetical digits, a run ended by a success
    ("Alert when eleven (11) or more failed sign-ins for one account occur within twenty (20) seconds and then a sign-in succeeds.", (B1, 11, 20)),
    ("A run of failed logins - 8 or more - against one account inside 15 minutes, ended by a successful login for that account.", (B1, 8, 900)),
    # key/value and table layouts
    ("name: guess-then-in\nevent: failed login (per account)\ncount: 5 or more\nwindow: 90 seconds\nthen: successful login\n", (B1, 5, 90)),
    ("| Setting | Value |\n|---|---|\n| Failure threshold | 4 |\n| Window | 2 minutes |\n| Then | successful login for the same account |", (B1, 4, 120)),
    # windows: bounded, glued, labelled
    ("Alert when 6 or more distinct accounts fail from a single source host under 20min.", (B2, 6, 1200)),
    ("| Group by | source host |\n| Distinct accounts | 5 |\n| Time window | 15 min |\n| Event | failed login |", (B2, 5, 900)),
    ("Count distinct accounts with failed logins per host over a sliding 10-minute window; alert at 6.", (B2, 6, 600)),
    ("The same account with successful logins from >= 4 different hosts within 1 hour should be flagged.", (B3, 4, 3600)),
    ("rule: shared-credentials\nevent: successful login\ngroup by: account\ndistinct hosts: 3 or more\nwindow: 20 minutes\n", (B3, 3, 1200)),
])
def test_windowed_phrasing_classes_compile_to_the_right_rule(text, expected):
    assert rule(text) == expected


@pytest.mark.parametrize("text,behaviour", [
    ("Flag a successful login when the authentication method is not the one the identity store lists for the account. "
     "Fields: account_id, event_type, auth_method and expected_auth_method.", B4),
    ("We need an alert for any login whose method contradicts what the account is supposed to use. Data: the login log and the identity export.", B4),
    ("| Compare | auth_method vs policy.expected_auth_method |\n| Fire when | values differ |\n| Event | successful login |", B4),
    ("Alert when a login succeeds without using MFA for an account whose policy says MFA is required.", B5),
    ("Raise an alert if the login is a success but the user did not use the second factor, and policy says this account must use MFA.", B5),
    ("| Event | successful login |\n| mfa_used | false |\n| policy mfa_required | true |\n| Result | alert |", B5),
])
def test_policy_phrasing_classes_compile_to_the_right_behaviour(text, behaviour):
    a = analyze(text, None)
    assert a.compiled is not None and a.compiled["behaviourId"] == behaviour, [(r.code, r.message[:90]) for r in a.reconciliation.reasons]


# ------------------------------------------------------------------------------------------------ safety
@pytest.mark.parametrize("text,why", [
    ("Alert when -5 or more failed logins hit one account within 3 minutes and then it succeeds.", "negative count"),
    ("Alert when -2 distinct accounts fail from one host within 10 minutes.", "negative count (distinct)"),
    ("Alert when 4 or more failed logins hit one account within 10 minutes or perhaps 10 seconds, then it succeeds.", "hedged alternative window"),
    ("Alert on 4 or more failed logins for one account within 5 minutes (that is, 50 minutes) and then a success.", "uncued second duration"),
    ("We do not want an alert when 6 or more failed logins hit one account within 2 minutes; helpdesk resets cause it.", "negated intent"),
    ("We explicitly do NOT want anything to fire when 4 or more distinct accounts fail from one host within 10 minutes.", "negated intent (distinct)"),
    ("If one host fails against 3 different accounts in 2 minutes we should alert.", "comparator unspecified"),
    ("Alert when fewer than 5 failed logins hit one account within 2 minutes and then it succeeds.", "upper bound"),
])
def test_safety_classes_never_compile(text, why):
    a = analyze(text, None)
    assert a.compiled is None, f"{why}: compiled as {a.compiled and {k: a.compiled[k] for k in ('behaviourId','countThreshold','distinctThreshold','timeWindowSeconds')}}"


def test_the_hedge_is_named_in_the_reason():
    a = analyze("Alert when 4 or more failed logins hit one account within 10 minutes or perhaps 10 seconds, then it succeeds.", None)
    assert "WINDOW_UNRESOLVED_ALTERNATIVE" in a.reconciliation.codes and a.status == "needs_review"


def test_negative_count_is_rejected_not_reviewed():
    claim = {"behaviourId": B1, "threshold": {"failureCount": 5}, "timeWindow": {"amount": 3, "unit": "minutes"}}
    a = analyze("Alert when -5 or more failed logins hit one account within 3 minutes and then it succeeds.", claim)
    assert a.status == "rejected" and "COUNT_INVALID_NUMBER" in a.reconciliation.codes


def test_a_negated_sentence_does_not_hide_a_real_rule_elsewhere():
    t = ("We do not want an alert on 2 failed logins - that is normal typo noise. "
         "Alert when 6 or more failed logins hit one account within 3 minutes and then it succeeds.")
    assert rule(t) == (B1, 6, 180)


def test_no_fewer_than_is_not_mistaken_for_a_negation():
    assert rule("Trigger an alert when no fewer than 5 failed logins hit one account within 2 minutes and then it succeeds.") == (B1, 5, 120)


def test_well_under_a_minute_is_still_an_approximation_not_a_window():
    t = ("It touched many hosts in well under a minute. "
         "Alert when 4 or more failed logins hit one account within 5 minutes and then it succeeds.")
    assert rule(t) == (B1, 4, 300)
