"""Golden scenarios (hand-computed from the semantics) and metamorphic properties for the reference engine.

Why this file exists: the differential test proves the Spark executor and the reference engine AGREE.
Agreement cannot expose a misunderstanding both share. So every expectation below was worked out on paper
from docs/spec/detection-semantics.md, and the properties (shift, permutation, redelivery, monotonicity)
need no second implementation at all. The Spark twins of the golden cases live in
compiler/src/test/scala/.../RuleCompilerSpec.scala and tests/integration/test_spark_agreement.py.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "verify"))

from scenarios import BASE, US, gen_scenarios  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402
from sentinelforge.compile import compile_spec  # noqa: E402
from sentinelforge.refengine import parse_micros, render_micros, run_reference  # noqa: E402


def rule(bid, threshold=None, window=None):
    beh = B.BEHAVIOURS[bid]
    spec = {"behaviourId": bid}
    if beh.recipe != B.POLICY_COMPARE:
        spec["threshold"] = {next(iter(beh.threshold_aliases)): threshold}
        spec["timeWindow"] = {"amount": window, "unit": "seconds"}
    return compile_spec(spec)


def ev(i, ts, acct="a", etype="login_failure", host="h1", mfa=False, method="password"):
    return {"event_id": f"e{i}", "timestamp": ts, "account_id": acct, "event_type": etype, "source_host": host,
            "source_ip": None, "auth_method": method, "mfa_used": mfa, "session_id": None}


B1 = "repeated-failed-login-then-success"
B2, B3 = "password-spray-across-accounts", "multi-host-authentication"
B4, B5 = "auth-method-policy-violation", "mfa-missing-on-required-account"


# ------------------------------------------------------------------------------- timestamp handling

class TestTimestamps:
    def test_microsecond_precision_is_exact(self):
        assert parse_micros("2026-03-01T00:00:00.000001Z") == parse_micros("2026-03-01T00:00:00Z") + 1
        assert parse_micros("1970-01-01T00:00:01.5Z") == 1_500_000

    def test_offsets_are_normalised_to_utc(self):
        assert parse_micros("2026-03-01T02:00:00+02:00") == parse_micros("2026-03-01T00:00:00Z")
        assert parse_micros("2026-02-28T19:00:00-05:00") == parse_micros("2026-03-01T00:00:00Z")

    @pytest.mark.parametrize("bad", ["", "2026-03-01", "2026-03-01 00:00:00Z", "2026-03-01T00:00:00", "2026-13-01T00:00:00Z",
                                     "2026-03-01T24:00:00Z", "2026-03-01T00:00:00.1234567Z", "yesterday", None, 17, "1709251200"])
    def test_malformed_or_ambiguous_timestamps_are_none(self, bad):
        assert parse_micros(bad) is None

    def test_render_truncates_to_milliseconds_in_utc(self):
        assert render_micros(parse_micros("2026-03-01T00:00:00.123456Z")) == "2026-03-01T00:00:00.123Z"


# ------------------------------------------------------------------------------------ golden: B1

class TestSequenceThenTrigger:
    R = rule(B1, 3, 10)

    def test_failure_exactly_W_before_is_inside(self):
        events = [ev(1, "2026-03-01T00:00:00.000Z"), ev(2, "2026-03-01T00:00:05.000Z"), ev(3, "2026-03-01T00:00:10.000Z"),
                  ev(4, "2026-03-01T00:00:10.000Z", etype="login_success")]
        out = run_reference(self.R, events).alerts
        assert [(a["triggeringEventId"], a["matchedCount"], a["windowStart"]) for a in out] == [("e4", 3, "2026-03-01T00:00:00.000Z")]
        assert out[0]["evidence"] == ["e1", "e2", "e3"]

    def test_one_microsecond_outside_is_outside(self):
        events = [ev(1, "2026-03-01T00:00:00.000000Z"), ev(2, "2026-03-01T00:00:05Z"), ev(3, "2026-03-01T00:00:09Z"),
                  ev(4, "2026-03-01T00:00:10.000001Z", etype="login_success")]
        assert run_reference(self.R, events).alerts == []

    def test_one_millisecond_makes_the_difference(self):
        """Second-truncation would put both successes in the same second and treat them identically."""
        base = [ev(1, "2026-03-01T00:00:00.999Z"), ev(2, "2026-03-01T00:00:05Z"), ev(3, "2026-03-01T00:00:06Z")]
        inside = base + [ev(4, "2026-03-01T00:00:10.999Z", etype="login_success")]
        outside = base + [ev(4, "2026-03-01T00:00:11.000Z", etype="login_success")]
        assert len(run_reference(self.R, inside).alerts) == 1
        assert run_reference(self.R, outside).alerts == []

    def test_redelivery_does_not_inflate_the_count(self):
        events = [ev(1, "2026-03-01T00:00:00Z"), ev(1, "2026-03-01T00:00:00Z"), ev(2, "2026-03-01T00:00:01Z"),
                  ev(9, "2026-03-01T00:00:02Z", etype="login_success"), ev(9, "2026-03-01T00:00:02Z", etype="login_success")]
        r = run_reference(self.R, events)
        assert r.alerts == [] and r.stats["duplicatesDropped"] == 2          # only 2 real failures < 3

    def test_two_real_incidents_each_alert_once(self):
        events = []
        for base, n in ((0, 1), (3600, 10)):
            ts = lambda s: render_micros(BASE + (base + s) * US)               # noqa: E731
            events += [ev(n, ts(0)), ev(n + 1, ts(1)), ev(n + 2, ts(2)), ev(n + 3, ts(3), etype="login_success")]
        assert [a["triggeringEventId"] for a in run_reference(self.R, events).alerts] == ["e4", "e13"]

    def test_malformed_and_missing_timestamps_are_quarantined_not_counted(self):
        events = [ev(1, "2026-03-01T00:00:00Z"), ev(2, "garbage"), ev(3, None), ev(4, "2026-03-01T00:00:03"),
                  ev(5, "2026-03-01T00:00:04Z", etype="login_success")]
        r = run_reference(self.R, events)
        assert r.alerts == []
        assert dict(r.stats["quarantineByReason"]) == {"malformed_timestamp": 2, "null_timestamp": 1}

    def test_a_different_account_does_not_contribute(self):
        events = [ev(1, "2026-03-01T00:00:00Z", acct="x"), ev(2, "2026-03-01T00:00:01Z", acct="y"), ev(3, "2026-03-01T00:00:02Z", acct="z"),
                  ev(4, "2026-03-01T00:00:03Z", acct="x", etype="login_success")]
        assert run_reference(self.R, events).alerts == []

    def test_success_without_failures_and_failures_without_success_do_not_alert(self):
        assert run_reference(self.R, [ev(1, "2026-03-01T00:00:00Z", etype="login_success")]).alerts == []
        assert run_reference(self.R, [ev(i, f"2026-03-01T00:00:0{i}Z") for i in range(1, 6)]).alerts == []


# ------------------------------------------------------------------------------- golden: B2 / B3

class TestDistinctCount:
    def test_rising_edge_gives_one_alert_per_incident(self):
        r = rule(B2, 3, 60)
        ts = lambda s: render_micros(BASE + s * US)                                        # noqa: E731
        events = [ev(1, ts(0), acct="a"), ev(2, ts(1), acct="b"), ev(3, ts(2), acct="c"),      # incident 1 breaches at e3
                  ev(4, ts(3), acct="d"),                                                        # still breaching: no new alert
                  ev(5, ts(500), acct="a"), ev(6, ts(501), acct="b"), ev(7, ts(502), acct="c")]  # incident 2 breaches at e7
        out = run_reference(r, events).alerts
        assert [(a["triggeringEventId"], a["matchedCount"]) for a in out] == [("e3", 3), ("e7", 3)]
        assert out[0]["evidence"] == ["e1", "e2", "e3"]

    def test_repeated_values_do_not_count_as_distinct(self):
        r = rule(B2, 3, 60)
        events = [ev(i, f"2026-03-01T00:00:0{i}Z", acct="same") for i in range(1, 6)]
        assert run_reference(r, events).alerts == []

    def test_peers_sharing_a_timestamp_are_all_inside_the_window(self):
        r = rule(B2, 3, 60)
        t = "2026-03-01T00:00:00.000Z"
        events = [ev(1, t, acct="a"), ev(2, t, acct="b"), ev(3, t, acct="c")]
        out = run_reference(r, events).alerts
        assert len(out) == 1 and out[0]["matchedCount"] == 3

    def test_multi_host_counts_distinct_hosts_of_successes_per_account(self):
        r = rule(B3, 2, 900)
        events = [ev(1, "2026-03-01T00:00:00Z", etype="login_success", host="h1"),
                  ev(2, "2026-03-01T00:05:00Z", etype="login_success", host="h1"),          # same host: no
                  ev(3, "2026-03-01T00:10:00Z", etype="login_success", host="h2")]          # second host inside 15 min
        out = run_reference(r, events).alerts
        assert [(a["groupKey"], a["triggeringEventId"], a["matchedCount"]) for a in out] == [("a", "e3", 2)]

    def test_failures_do_not_count_for_multi_host(self):
        r = rule(B3, 2, 900)
        events = [ev(1, "2026-03-01T00:00:00Z", host="h1"), ev(2, "2026-03-01T00:01:00Z", host="h2")]
        assert run_reference(r, events).alerts == []

    def test_a_host_change_after_the_window_is_a_new_observation_not_an_overlap(self):
        r = rule(B3, 2, 60)
        events = [ev(1, "2026-03-01T00:00:00Z", etype="login_success", host="h1"),
                  ev(2, "2026-03-01T00:01:01Z", etype="login_success", host="h2")]
        assert run_reference(r, events).alerts == []


# ---------------------------------------------------------------------------------- golden: policy

def pol(acct, **kw):
    return {"account_id": acct, **kw}


class TestPolicyCompare:
    def test_mfa_alert_and_compliance(self):
        r = rule(B5)
        events = [ev(1, "2026-03-01T00:00:00Z", acct="need", etype="login_success", mfa=False),
                  ev(2, "2026-03-01T00:00:01Z", acct="need", etype="login_success", mfa=True),
                  ev(3, "2026-03-01T00:00:02Z", acct="free", etype="login_success", mfa=False)]
        res = run_reference(r, events, [pol("need", mfa_required=True), pol("free", mfa_required=False)])
        assert [(a["triggeringEventId"], a["status"]) for a in res.all_results] == [("e1", "alert"), ("e2", "no_alert"), ("e3", "no_alert")]
        assert [a["triggeringEventId"] for a in res.alerts] == ["e1"]

    def test_duplicate_identical_policy_rows_collapse(self):
        r = rule(B5)
        events = [ev(1, "2026-03-01T00:00:00Z", acct="a", etype="login_success", mfa=False)]
        res = run_reference(r, events, [pol("a", mfa_required=True)] * 3)
        assert len(res.alerts) == 1

    def test_conflicting_policy_rows_are_insufficient_context(self):
        r = rule(B5)
        events = [ev(1, "2026-03-01T00:00:00Z", acct="a", etype="login_success", mfa=False)]
        res = run_reference(r, events, [pol("a", mfa_required=True), pol("a", mfa_required=False)])
        assert [(a["status"], a["reason"]) for a in res.alerts] == [("insufficient_context", "policy_conflict")]

    def test_missing_policy_and_null_values_degrade(self):
        r = rule(B4)
        events = [ev(1, "2026-03-01T00:00:00Z", acct="ghost", etype="login_success"),
                  ev(2, "2026-03-01T00:00:01Z", acct="a", etype="login_success", method=None),
                  ev(3, "2026-03-01T00:00:02Z", acct="b", etype="login_success", method="token")]
        res = run_reference(r, events, [pol("a", expected_auth_method="password"), pol("b", expected_auth_method=None)])
        assert {a["triggeringEventId"]: a["reason"] for a in res.all_results} == {
            "e1": "no_policy_record", "e2": "log_value_null", "e3": "policy_value_null"}

    def test_auth_method_mismatch_is_any_direction(self):
        r = rule(B4)
        events = [ev(1, "2026-03-01T00:00:00Z", acct="a", etype="login_success", method="token"),
                  ev(2, "2026-03-01T00:00:01Z", acct="a", etype="login_success", method="certificate"),
                  ev(3, "2026-03-01T00:00:02Z", acct="a", etype="login_success", method="password")]
        res = run_reference(r, events, [pol("a", expected_auth_method="password")])
        assert [a["triggeringEventId"] for a in res.alerts] == ["e1", "e2"]


# ------------------------------------------------------------------------------------ metamorphic

def _alert_signature(alerts, shift_us=0):
    return sorted((a["groupKey"], a["triggeringEventId"], a["matchedCount"] if "matchedCount" in a else a.get("status"),
                   tuple(a.get("evidence", []))) for a in alerts)


WINDOWED = [B1, B2, B3]


def _micros_text(micros: int) -> str:
    """Microsecond-exact UTC rendering (render_micros is millisecond-resolution by design)."""
    secs, us = divmod(micros, US)
    import datetime as _dt
    return _dt.datetime.fromtimestamp(secs, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{us:06d}Z"


@pytest.mark.parametrize("bid", WINDOWED)
@pytest.mark.parametrize("seed", range(12))
class TestMetamorphicProperties:
    def _setup(self, bid, seed):
        cfgs = gen_scenarios(bid, 6, seed)
        (w, t), scs = next(iter(cfgs.items()))
        return rule(bid, t, w), scs, w

    def test_shifting_every_timestamp_changes_nothing_but_the_timestamps(self, bid, seed):
        r, scs, w = self._setup(bid, seed)
        delta = 86400 * 37 * US + 250_000                      # 37 days and a quarter second
        for s in scs:
            shifted = []
            for e in s.events:
                m = parse_micros(e["timestamp"])
                shifted.append({**e, "timestamp": _micros_text(m + delta)} if m is not None else dict(e))
            assert _alert_signature(run_reference(r, s.events).alerts) == _alert_signature(run_reference(r, shifted).alerts)

    def test_arrival_order_never_matters(self, bid, seed):
        r, scs, _ = self._setup(bid, seed)
        rng = random.Random(seed)
        for s in scs:
            events = list(s.events)
            a = run_reference(r, events)
            rng.shuffle(events)
            b = run_reference(r, events)
            assert _alert_signature(a.alerts) == _alert_signature(b.alerts) and a.stats == b.stats

    def test_redelivering_events_changes_no_alert(self, bid, seed):
        r, scs, _ = self._setup(bid, seed)
        for s in scs:
            a = run_reference(r, s.events)
            b = run_reference(r, s.events + [dict(e) for e in s.events[::2]])
            assert _alert_signature(a.alerts) == _alert_signature(b.alerts)

    def test_unrelated_entities_cannot_change_an_alert(self, bid, seed):
        r, scs, _ = self._setup(bid, seed)
        target = scs[0]
        alone = run_reference(r, target.events)
        crowd = run_reference(r, [e for s in scs for e in s.events])
        mine = [a for a in crowd.alerts if a["groupKey"].startswith(target.id + "-")]
        assert _alert_signature(alone.alerts) == _alert_signature(mine)


@pytest.mark.parametrize("seed", range(20))
def test_a_larger_threshold_never_creates_new_sequence_alerts(seed):
    (w, t), scs = next(iter(gen_scenarios(B1, 4, seed).items()))
    for s in scs:
        loose = {a["triggeringEventId"] for a in run_reference(rule(B1, t, w), s.events).alerts}
        strict = {a["triggeringEventId"] for a in run_reference(rule(B1, t + 1, w), s.events).alerts}
        assert strict <= loose


@pytest.mark.parametrize("seed", range(20))
def test_a_wider_window_never_loses_sequence_alerts(seed):
    (w, t), scs = next(iter(gen_scenarios(B1, 4, seed).items()))
    for s in scs:
        narrow = {a["triggeringEventId"] for a in run_reference(rule(B1, t, w), s.events).alerts}
        wide = {a["triggeringEventId"] for a in run_reference(rule(B1, t, w + 5), s.events).alerts}
        assert narrow <= wide
