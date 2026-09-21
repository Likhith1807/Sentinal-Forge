"""Labelled B1-B5 incident injection.

Each builder constructs a small, self-contained incident on entities that no
other incident or background event uses, and records its ground-truth label
*from how it was built* -- never by running a detector. Kinds per behaviour
cover: typical positives, exact-boundary positives, exact-boundary negatives
(one second / one unit past the closed window or threshold), and hard
negatives that a sloppy rule would get wrong (wrong grouping key, wrong event
type, wrong ordering, counting events instead of distinct values).

Window arithmetic follows docs/spec/detection-semantics.md: windows are
closed on both ends and measured in whole seconds, so every incident event is
placed on a whole second (``.000``).

Every incident's own events are policy-compliant except where the incident is
specifically a B4/B5 violation, so an incident for one behaviour never
produces an accidental alert for another. ``refdetect`` verifies that.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from .params import B1, B2, B3, B4, B5, DetectionParams
from .policy import AUTH_METHODS

ALERT, NO_ALERT, INSUFFICIENT = "alert", "no_alert", "insufficient_context"


def iso(epoch_sec: int) -> str:
    return dt.datetime.fromtimestamp(epoch_sec, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


@dataclass
class Context:
    rng: random.Random
    params: DetectionParams
    start_sec: int
    end_sec: int
    events: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    policy: list = field(default_factory=list)
    _counters: dict = field(default_factory=lambda: {"acct": 0, "host": 0, "event": 0, "incident": 0})
    _ts: dict = field(default_factory=dict)   # event_id -> ISO timestamp, for O(1) label windows

    # -- entity / event allocation ------------------------------------------------
    def account(self, policy: tuple | None = ("human", "password", False)) -> str:
        """Allocate a fresh account; ``policy=None`` leaves it absent from the policy table."""
        self._counters["acct"] += 1
        account_id = f"ixu{self._counters['acct']:07d}"
        if policy is not None:
            account_type, method, mfa_required = policy
            self.policy.append({"account_id": account_id, "account_type": account_type,
                                "expected_auth_method": method, "mfa_required": mfa_required})
        return account_id

    def host(self) -> str:
        self._counters["host"] += 1
        return f"ixh{self._counters['host']:07d}"

    def event(self, sec: int, account: str, etype: str, host: str,
              method: str = "password", mfa: bool = False) -> str:
        self._counters["event"] += 1
        event_id = f"e{self._counters['event']:012d}"
        self._ts[event_id] = iso(sec)
        self.events.append({
            "event_id": event_id, "timestamp": self._ts[event_id], "account_id": account,
            "event_type": etype, "source_host": host, "source_ip": None,
            "auth_method": method, "mfa_used": mfa,
            "session_id": f"s{self._counters['event']:012d}" if etype == "login_success" else None,
        })
        return event_id

    def t0(self, span_s: int) -> int:
        """A random whole-second start with ``span_s`` of room after it, inside the data range."""
        low = self.start_sec + 3600
        high = self.end_sec - 3600 - span_s
        if high <= low:
            raise ValueError("Data range too short for incident placement; increase --days.")
        return self.rng.randint(low, high)

    def label(self, behaviour: str, kind: str, status: str, group_keys: list[str],
              event_ids: list[str], trigger: str | None, note: str) -> None:
        self._counters["incident"] += 1
        stamps = sorted(self._ts[event_id] for event_id in event_ids)
        self.labels.append({
            "incidentId": f"inc-{self._counters['incident']:07d}",
            "behaviourId": behaviour,
            "kind": kind,
            "expectedStatus": status,
            "expectedAlert": status == ALERT,
            "groupKeys": group_keys,
            "expectedTriggerEventId": trigger,
            "eventIds": event_ids,
            "windowStart": stamps[0], "windowEnd": stamps[-1],
            "note": note,
        })


def _distinct_seconds(rng: random.Random, low: int, high: int, n: int) -> list[int]:
    """``n`` distinct whole seconds in ``[low, high]`` (inclusive), ascending."""
    return sorted(rng.sample(range(low, high + 1), n))


# ---------------------------------------------------------------------------
# B1: repeated failed login, then success (SequenceThenTrigger)
# ---------------------------------------------------------------------------
def _b1_events(c: Context, account, host, fail_secs, success_sec):
    ids = [c.event(s, account, "login_failure", host) for s in fail_secs]
    trigger = c.event(success_sec, account, "login_success", host) if success_sec is not None else None
    return ids, trigger


def b1_pos_typical(c: Context):
    W, T = c.params.b1_window_s, c.params.b1_fail_threshold
    a, h = c.account(), c.host()
    ts = c.t0(W + 60) + W
    fails = _distinct_seconds(c.rng, ts - W, ts - 1, c.rng.randint(T, T + 4))
    ids, trig = _b1_events(c, a, h, fails, ts)
    c.label(B1, "positive", ALERT, [a], ids + [trig], trig, f"{len(fails)} failures within {W}s, then a success.")


def b1_pos_boundary(c: Context):
    W, T = c.params.b1_window_s, c.params.b1_fail_threshold
    a, h = c.account(), c.host()
    ts = c.t0(W + 60) + W
    fails = [ts - W] + _distinct_seconds(c.rng, ts - W + 1, ts - 1, T - 1)
    ids, trig = _b1_events(c, a, h, fails, ts)
    c.label(B1, "boundary-positive", ALERT, [a], ids + [trig], trig,
            f"Oldest of exactly {T} failures sits at exactly {W}s before the success (closed window).")


def b1_neg_boundary(c: Context):
    W, T = c.params.b1_window_s, c.params.b1_fail_threshold
    a, h = c.account(), c.host()
    ts = c.t0(W + 60) + W
    fails = [ts - W - 1] + _distinct_seconds(c.rng, ts - W, ts - 1, T - 1)
    ids, trig = _b1_events(c, a, h, fails, ts)
    c.label(B1, "boundary-negative", NO_ALERT, [a], ids + [trig], None,
            f"{T} failures but the oldest is {W + 1}s before the success: only {T - 1} fall in the window.")


def b1_neg_threshold(c: Context):
    W, T = c.params.b1_window_s, c.params.b1_fail_threshold
    a, h = c.account(), c.host()
    ts = c.t0(W + 60) + W
    fails = _distinct_seconds(c.rng, ts - W, ts - 1, T - 1)
    ids, trig = _b1_events(c, a, h, fails, ts)
    c.label(B1, "hard-negative", NO_ALERT, [a], ids + [trig], None, f"Only {T - 1} failures (threshold - 1) before the success.")


def b1_neg_no_success(c: Context):
    W, T = c.params.b1_window_s, c.params.b1_fail_threshold
    a, h = c.account(), c.host()
    base = c.t0(W + 60)
    fails = _distinct_seconds(c.rng, base, base + W, T + 1)
    ids, _ = _b1_events(c, a, h, fails, None)
    c.label(B1, "hard-negative", NO_ALERT, [a], ids, None, "A failed attack that never succeeded: no success event, so no trigger.")


def b1_neg_spread(c: Context):
    W, T = c.params.b1_window_s, c.params.b1_fail_threshold
    a, h = c.account(), c.host()
    ts = c.t0(W + 900) + W + 600
    in_window = _distinct_seconds(c.rng, ts - W, ts - 1, T - 2)
    earlier = _distinct_seconds(c.rng, ts - W - 600, ts - W - 1, T + 3)
    ids, trig = _b1_events(c, a, h, earlier + in_window, ts)
    c.label(B1, "hard-negative", NO_ALERT, [a], ids + [trig], None,
            f"{T + 1 + T - 2} failures overall but spread over ~{(W + 600) // 60} minutes; only {T - 2} inside the window.")


def b1_neg_order(c: Context):
    W, T = c.params.b1_window_s, c.params.b1_fail_threshold
    a, h = c.account(), c.host()
    ts = c.t0(W + 60)
    first = c.event(ts, a, "login_success", h)
    fails = _distinct_seconds(c.rng, ts + 1, ts + W, T + 1)
    ids = [c.event(s, a, "login_failure", h) for s in fails]
    c.label(B1, "hard-negative", NO_ALERT, [a], [first] + ids, None, "Success first, failures afterwards: order matters, no success follows the failures.")


def b1_neg_wrong_account(c: Context):
    W, T = c.params.b1_window_s, c.params.b1_fail_threshold
    a, other, h = c.account(), c.account(), c.host()
    ts = c.t0(W + 60) + W
    fails = _distinct_seconds(c.rng, ts - W, ts - 1, T + 1)
    ids, _ = _b1_events(c, a, h, fails, None)
    trig = c.event(ts, other, "login_success", h)
    c.label(B1, "hard-negative", NO_ALERT, [a, other], ids + [trig], None,
            "Failures on one account, the success on a different account: grouping key must be account_id.")


# ---------------------------------------------------------------------------
# B2: password spray (DistinctCountWithinWindow over failures, by source_host)
# ---------------------------------------------------------------------------
def _first_breach(ordered_event_ids: list[str], ordered_accounts: list[str], threshold: int) -> str:
    """Event at which the running distinct-account count first reaches ``threshold``.

    Only valid when every event falls inside one window, so the running set is the window set.
    """
    seen: set[str] = set()
    for event_id, account in zip(ordered_event_ids, ordered_accounts):
        seen.add(account)
        if len(seen) >= threshold:
            return event_id
    raise AssertionError("construction never reaches the distinct threshold")


def b2_pos_typical(c: Context):
    W, T = c.params.b2_window_s, c.params.b2_distinct_threshold
    h = c.host()
    d = c.rng.randint(T, T + 4)
    accounts = [c.account() for _ in range(d)]
    n_events = d + c.rng.randint(0, d)
    base = c.t0(W + 60)
    secs = _distinct_seconds(c.rng, base, base + W, n_events)
    assignment = accounts + [c.rng.choice(accounts) for _ in range(n_events - d)]
    c.rng.shuffle(assignment)
    ids = [c.event(s, acct, "login_failure", h) for s, acct in zip(secs, assignment)]
    trig = _first_breach(ids, assignment, T)
    c.label(B2, "positive", ALERT, [h], ids, trig, f"{d} distinct accounts failed from one host within {W}s.")


def b2_pos_boundary(c: Context):
    W, T = c.params.b2_window_s, c.params.b2_distinct_threshold
    h = c.host()
    accounts = [c.account() for _ in range(T)]
    base = c.t0(W + 60)
    secs = [base] + _distinct_seconds(c.rng, base + 1, base + W - 1, T - 2) + [base + W]
    ids = [c.event(s, acct, "login_failure", h) for s, acct in zip(secs, accounts)]
    c.label(B2, "boundary-positive", ALERT, [h], ids, ids[-1],
            f"The {T}th distinct account arrives exactly {W}s after the first (closed window).")


def b2_neg_boundary(c: Context):
    W, T = c.params.b2_window_s, c.params.b2_distinct_threshold
    h = c.host()
    accounts = [c.account() for _ in range(T)]
    base = c.t0(W + 60)
    secs = [base] + _distinct_seconds(c.rng, base + 1, base + W, T - 2) + [base + W + 1]
    ids = [c.event(s, acct, "login_failure", h) for s, acct in zip(secs, accounts)]
    c.label(B2, "boundary-negative", NO_ALERT, [h], ids, None,
            f"The {T}th account arrives {W + 1}s after the first: only {T - 1} distinct accounts in any window.")


def b2_neg_threshold(c: Context):
    W, T = c.params.b2_window_s, c.params.b2_distinct_threshold
    h = c.host()
    accounts = [c.account() for _ in range(T - 1)]
    n_events = 2 * (T - 1) + 2
    base = c.t0(W + 60)
    secs = _distinct_seconds(c.rng, base, base + W, n_events)
    assignment = accounts + accounts + [c.rng.choice(accounts) for _ in range(n_events - 2 * len(accounts))]
    c.rng.shuffle(assignment)
    ids = [c.event(s, acct, "login_failure", h) for s, acct in zip(secs, assignment)]
    c.label(B2, "hard-negative", NO_ALERT, [h], ids, None,
            f"{n_events} failures but only {T - 1} distinct accounts (counting events instead of distinct accounts would alert).")


def b2_neg_single_account(c: Context):
    W = c.params.b2_window_s
    h, a = c.host(), c.account()
    base = c.t0(W + 60)
    secs = _distinct_seconds(c.rng, base, base + W, 12)
    ids = [c.event(s, a, "login_failure", h) for s in secs]
    c.label(B2, "hard-negative", NO_ALERT, [h], ids, None, "One account failing 12 times from one host is brute force (B1), not a spray.")


def b2_neg_success_events(c: Context):
    W, T = c.params.b2_window_s, c.params.b2_distinct_threshold
    h = c.host()
    accounts = [c.account() for _ in range(T + 2)]
    base = c.t0(W + 60)
    secs = _distinct_seconds(c.rng, base, base + W, len(accounts))
    ids = [c.event(s, acct, "login_success", h) for s, acct in zip(secs, accounts)]
    c.label(B2, "hard-negative", NO_ALERT, [h], ids, None, "Many distinct accounts, but successes not failures: event type must be login_failure.")


def b2_neg_slow(c: Context):
    W, T = c.params.b2_window_s, c.params.b2_distinct_threshold
    h = c.host()
    accounts = [c.account() for _ in range(T)]
    gap = W // 2 + 1
    base = c.t0(gap * T + 60)
    ids = [c.event(base + i * gap, acct, "login_failure", h) for i, acct in enumerate(accounts)]
    c.label(B2, "hard-negative", NO_ALERT, [h], ids, None,
            f"{T} distinct accounts but {gap}s apart: no {W}s window ever holds {T} of them (low-and-slow below the rule).")


# ---------------------------------------------------------------------------
# B3: concurrent sessions (DistinctCountWithinWindow over successes, by account)
# ---------------------------------------------------------------------------
def _pair(c: Context, gap: int, second_type="login_success", same_host=False):
    a, h1 = c.account(), c.host()
    h2 = h1 if same_host else c.host()
    t = c.t0(gap + 60)
    e1 = c.event(t, a, "login_success", h1)
    e2 = c.event(t + gap, a, second_type, h2)
    return a, e1, e2


def b3_pos_typical(c: Context):
    W = c.params.b3_window_s
    a, e1, e2 = _pair(c, c.rng.randint(1, W))
    c.label(B3, "positive", ALERT, [a], [e1, e2], e2, "Successful sessions from two different hosts within the window.")


def b3_pos_boundary(c: Context):
    W = c.params.b3_window_s
    a, e1, e2 = _pair(c, W)
    c.label(B3, "boundary-positive", ALERT, [a], [e1, e2], e2, f"Two hosts exactly {W}s apart (closed window).")


def b3_neg_boundary(c: Context):
    W = c.params.b3_window_s
    a, e1, e2 = _pair(c, W + 1)
    c.label(B3, "boundary-negative", NO_ALERT, [a], [e1, e2], None, f"Two hosts {W + 1}s apart: one second outside the window.")


def b3_neg_same_host(c: Context):
    W = c.params.b3_window_s
    a, e1, e2 = _pair(c, c.rng.randint(60, W), same_host=True)
    c.label(B3, "hard-negative", NO_ALERT, [a], [e1, e2], None, "Two successes inside the window from the SAME host: the rule keys on distinct hosts.")


def b3_neg_failure(c: Context):
    W = c.params.b3_window_s
    a, e1, e2 = _pair(c, c.rng.randint(1, W), second_type="login_failure")
    c.label(B3, "hard-negative", NO_ALERT, [a], [e1, e2], None, "Second host only produced a failure, not a session.")


def b3_neg_far_hosts(c: Context):
    W = c.params.b3_window_s
    a = c.account()
    gap = W + 1 + c.rng.randint(0, 300)
    t = c.t0(2 * gap + 60)
    ids = [c.event(t + i * gap, a, "login_success", c.host()) for i in range(3)]
    c.label(B3, "hard-negative", NO_ALERT, [a], ids, None, f"Three different hosts, each more than {W}s apart: never two inside one window.")


# ---------------------------------------------------------------------------
# B4: service account authenticating with an unexpected method (PolicyCompare)
# ---------------------------------------------------------------------------
def _other_method(rng: random.Random, expected: str) -> str:
    return rng.choice([m for m in AUTH_METHODS if m != expected])


def b4_pos(c: Context):
    expected = c.rng.choice(AUTH_METHODS)
    a, h = c.account(("service", expected, False)), c.host()
    e = c.event(c.t0(60), a, "login_success", h, method=_other_method(c.rng, expected))
    c.label(B4, "positive", ALERT, [a], [e], e, "Success with a method that differs from the policy's expected method.")


def b4_neg_compliant(c: Context):
    expected = c.rng.choice(AUTH_METHODS)
    a, h = c.account(("service", expected, False)), c.host()
    e = c.event(c.t0(60), a, "login_success", h, method=expected)
    c.label(B4, "hard-negative", NO_ALERT, [a], [e], None, "Success using exactly the expected method.")


def b4_neg_failure(c: Context):
    expected = c.rng.choice(AUTH_METHODS)
    a, h = c.account(("service", expected, False)), c.host()
    e = c.event(c.t0(60), a, "login_failure", h, method=_other_method(c.rng, expected))
    c.label(B4, "hard-negative", NO_ALERT, [a], [e], None, "Mismatched method but the login failed: the rule looks at successes only.")


def _no_policy_incident(c: Context):
    """A success by an account absent from the policy table.

    Both PolicyCompare rules (B4 and B5) join the same table, so both must degrade to
    insufficient_context for this event; labelling only one would leave the other's
    (correct) result looking like an unexpected alert.
    """
    a, h = c.account(None), c.host()
    e = c.event(c.t0(60), a, "login_success", h)
    for behaviour in (B4, B5):
        c.label(behaviour, "insufficient-context", INSUFFICIENT, [a], [e], e,
                "Account has no policy row: both policy-based rules must degrade to "
                "insufficient_context, not guess.")


def b4_insufficient(c: Context):
    _no_policy_incident(c)


# ---------------------------------------------------------------------------
# B5: success without MFA on an MFA-required account (PolicyCompare)
# ---------------------------------------------------------------------------
def b5_pos(c: Context):
    a, h = c.account(("human", "password", True)), c.host()
    e = c.event(c.t0(60), a, "login_success", h, mfa=False)
    c.label(B5, "positive", ALERT, [a], [e], e, "MFA required by policy, success with mfa_used=false.")


def b5_neg_compliant(c: Context):
    a, h = c.account(("human", "password", True)), c.host()
    e = c.event(c.t0(60), a, "login_success", h, mfa=True)
    c.label(B5, "hard-negative", NO_ALERT, [a], [e], None, "MFA required and used.")


def b5_neg_not_required(c: Context):
    a, h = c.account(("human", "password", False)), c.host()
    e = c.event(c.t0(60), a, "login_success", h, mfa=False)
    c.label(B5, "hard-negative", NO_ALERT, [a], [e], None, "No MFA on an account whose policy does not require it.")


def b5_neg_failure(c: Context):
    a, h = c.account(("human", "password", True)), c.host()
    e = c.event(c.t0(60), a, "login_failure", h, mfa=False)
    c.label(B5, "hard-negative", NO_ALERT, [a], [e], None, "No MFA on a failed login: only successes are policy violations.")


def b5_insufficient(c: Context):
    _no_policy_incident(c)


# Deliberately NOT injected: a NULL mfa_used / auth_method on a success. The
# compiler currently scores that as no_alert instead of insufficient_context
# (detection-semantics.md, case G); injecting it would encode a known gap as
# an expectation. It is added once that gap is fixed.

BUILDERS: dict[str, list] = {
    B1: [b1_pos_typical, b1_pos_boundary, b1_neg_boundary, b1_neg_threshold,
         b1_neg_no_success, b1_neg_spread, b1_neg_order, b1_neg_wrong_account],
    B2: [b2_pos_typical, b2_pos_boundary, b2_neg_boundary, b2_neg_threshold,
         b2_neg_single_account, b2_neg_success_events, b2_neg_slow],
    B3: [b3_pos_typical, b3_pos_boundary, b3_neg_boundary, b3_neg_same_host,
         b3_neg_failure, b3_neg_far_hosts],
    B4: [b4_pos, b4_neg_compliant, b4_neg_failure, b4_insufficient],
    B5: [b5_pos, b5_neg_compliant, b5_neg_not_required, b5_neg_failure, b5_insufficient],
}


def inject(c: Context, per_behaviour: int) -> None:
    """Add ``per_behaviour`` incidents for each behaviour, cycling through every kind evenly."""
    for behaviour, builders in BUILDERS.items():
        for i in range(per_behaviour):
            builders[i % len(builders)](c)
