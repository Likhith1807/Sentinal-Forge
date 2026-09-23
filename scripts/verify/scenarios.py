"""Adversarial scenario generation for differential and property testing.

Random scenarios are only as good as where they look. These generators deliberately put events ON the
edges the semantics turn on - one microsecond either side of a window boundary, identical timestamps,
redelivered and conflicting duplicates, malformed and zone-less timestamps, out-of-order arrival, NULL
keys, several incidents for one entity, missing / duplicated / conflicting policy rows - instead of
sampling timestamps uniformly and hoping to land there.

Every scenario lives in its own key namespace (`s<N>-...`) so hundreds can be executed in one Spark
job without interacting, then split apart again for comparison.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from sentinelforge import behaviours as B
from sentinelforge.refengine import render_micros

BASE = int(dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc).timestamp()) * 1_000_000
US = 1_000_000

WINDOWS = [1, 2, 5, 10, 30, 60, 300]
THRESHOLDS = [2, 3, 4]


@dataclass
class Scenario:
    id: str
    behaviourId: str
    events: list = field(default_factory=list)
    policy: list = field(default_factory=list)
    tags: list = field(default_factory=list)


def fmt_ts(micros: int, rng: random.Random) -> str:
    """Render an instant in one of several equivalent, valid RFC 3339 spellings."""
    secs, us = divmod(micros, US)
    t = dt.datetime.fromtimestamp(secs, dt.timezone.utc)
    base = t.strftime("%Y-%m-%dT%H:%M:%S")
    style = rng.choice(["ms", "ms", "ms", "us", "sec", "frac1", "offset"])
    if style == "sec":
        if us == 0:
            return base + "Z"
        style = "us"
    if style == "us":
        return f"{base}.{us:06d}Z"
    if style == "frac1" and us % 100_000 == 0:
        return f"{base}.{us // 100_000}Z"
    if style == "offset":
        hours = rng.choice([-5, -1, 1, 2, 5])
        local = t + dt.timedelta(hours=hours)
        sign = "+" if hours >= 0 else "-"
        frac = f".{us:06d}" if us else ""
        return f"{local.strftime('%Y-%m-%dT%H:%M:%S')}{frac}{sign}{abs(hours):02d}:00"
    return f"{base}.{us // 1000:03d}Z" if us % 1000 == 0 else f"{base}.{us:06d}Z"


def _ev(sc: str, n: int, ts: str, account, etype, host, method="password", mfa=False) -> dict:
    return {"event_id": f"{sc}-e{n:03d}", "timestamp": ts, "account_id": account, "event_type": etype,
            "source_host": host, "source_ip": None, "auth_method": method, "mfa_used": mfa, "session_id": None}


def _boundary_offsets(w: int, rng: random.Random) -> int:
    """A gap (microseconds) chosen to sit on or next to the window edge far more often than chance would."""
    edge = w * US
    return rng.choice([0, 0, 1, edge - 1, edge, edge, edge + 1, edge // 2, rng.randint(0, 2 * edge), 2 * edge + 7])


MALFORMED = ["not-a-timestamp", "2026-03-01T00:00:00", "2026-03-01 00:00:00Z", "2026-13-40T00:00:00.000Z",
             "2026-03-01T00:00:00.1234567Z", "", "2026-03-01T25:00:00.000Z", "1709251200"]


def _inject_noise(events: list, sc: str, rng: random.Random, key_cols: list[str], tags: list) -> list:
    out = list(events)
    n = len(out)
    counter = 900
    if out and rng.random() < 0.25:                        # exact redelivery
        out.append(dict(rng.choice(out)))
        tags.append("dup-exact")
    if out and rng.random() < 0.12:                        # conflicting duplicate: same id, different content
        victim = dict(rng.choice(out))
        victim["source_host"] = "other-host"
        victim["auth_method"] = "token"
        out.append(victim)
        tags.append("dup-conflict")
    if rng.random() < 0.2:                                 # malformed timestamps on otherwise valid rows
        base = dict(rng.choice(out)) if out else _ev(sc, 0, "x", f"{sc}-a", "login_failure", f"{sc}-h")
        counter += 1
        base["event_id"] = f"{sc}-e{counter}"
        base["timestamp"] = rng.choice(MALFORMED)
        out.append(base)
        tags.append("malformed-ts")
    if rng.random() < 0.08 and out:                        # NULL key column
        base = dict(rng.choice(out))
        counter += 1
        base["event_id"] = f"{sc}-e{counter}"
        base[rng.choice(key_cols)] = None
        out.append(base)
        tags.append("null-key")
    rng.shuffle(out)                                       # out-of-order arrival
    if n > 1:
        tags.append("shuffled")
    return out


def gen_sequence(rng: random.Random, idx: int, w: int, n: int, behaviour: B.Behaviour) -> Scenario:
    sc = f"s{idx}"
    acct = f"{sc}-acct"
    tags: list = []
    events, k = [], 0
    t = BASE + rng.randint(0, 3600) * US
    for _ in range(rng.randint(1, 3)):                     # several incidents for the same account
        trig = t + rng.choice([w * US, w * US + 3, 2 * w * US])
        for _f in range(rng.randint(max(0, n - 2), n + 2)):
            k += 1
            events.append(_ev(sc, k, fmt_ts(trig - _boundary_offsets(w, rng), rng), acct, "login_failure", f"{sc}-h1"))
        k += 1
        events.append(_ev(sc, k, fmt_ts(trig, rng), acct, "login_success", f"{sc}-h1"))
        if rng.random() < 0.3:                             # tie: a failure and a success on the same instant
            k += 1
            events.append(_ev(sc, k, fmt_ts(trig, rng), acct, "login_failure", f"{sc}-h1"))
            tags.append("tie")
        t = trig + rng.choice([w * US + 1, 5 * w * US, 60 * w * US])
    other = f"{sc}-other"
    for _ in range(rng.randint(0, 3)):
        k += 1
        events.append(_ev(sc, k, fmt_ts(t - rng.randint(0, w * US), rng), other, rng.choice(["login_failure", "login_success"]), f"{sc}-h2"))
    return Scenario(sc, behaviour.id, _inject_noise(events, sc, rng, ["account_id"], tags), [], tags)


def gen_distinct(rng: random.Random, idx: int, w: int, n: int, behaviour: B.Behaviour) -> Scenario:
    sc = f"s{idx}"
    group_col, distinct_col = behaviour.grouping_key, behaviour.distinct_field
    tags: list = []
    events = []
    group = f"{sc}-{group_col[:4]}"
    pool = [f"{sc}-{distinct_col[:4]}{i}" for i in range(rng.randint(1, n + 2))]
    t = BASE + rng.randint(0, 3600) * US
    for k in range(1, rng.randint(2, 14)):
        # dense clusters most of the time (so breaches and their rising edges actually occur), boundary-hugging gaps the rest
        t += rng.randint(0, max(1, w * US // 3)) if rng.random() < 0.6 else _boundary_offsets(w, rng)
        e = _ev(sc, k, fmt_ts(t, rng), f"{sc}-acct-x", behaviour.filter_event_type, f"{sc}-host-x")
        e[group_col], e[distinct_col] = group, rng.choice(pool)
        events.append(e)
        if rng.random() < 0.15:                            # an irrelevant event type interleaved
            noise = dict(e)
            noise["event_id"] = f"{sc}-n{k:03d}"
            noise["event_type"] = "login_success" if behaviour.filter_event_type == "login_failure" else "login_failure"
            events.append(noise)
        if rng.random() < 0.2:                             # same-instant peer
            tie = dict(e)
            tie["event_id"] = f"{sc}-t{k:03d}"
            tie[distinct_col] = rng.choice(pool)
            events.append(tie)
            tags.append("tie")
    return Scenario(sc, behaviour.id, _inject_noise(events, sc, rng, [group_col, distinct_col], tags), [], tags)


def gen_policy(rng: random.Random, idx: int, behaviour: B.Behaviour) -> Scenario:
    sc = f"s{idx}"
    tags: list = []
    accounts = [f"{sc}-a{i}" for i in range(rng.randint(1, 3))]
    policy, events, k = [], [], 0
    for a in accounts:
        variant = rng.choice(["one", "one", "one", "none", "dup-same", "dup-conflict", "null-value"])
        tags.append(f"policy-{variant}")
        if behaviour.policy_field == "mfa_required":
            v1, v2 = rng.choice([True, False]), None
            v2 = not v1
        else:
            v1, v2 = rng.choice(["password", "token", "certificate"]), None
            v2 = rng.choice([m for m in ["password", "token", "certificate"] if m != v1])
        if variant == "one":
            policy.append({"account_id": a, behaviour.policy_field: v1})
        elif variant == "dup-same":
            policy += [{"account_id": a, behaviour.policy_field: v1}] * 2
        elif variant == "dup-conflict":
            policy += [{"account_id": a, behaviour.policy_field: v1}, {"account_id": a, behaviour.policy_field: v2}]
        elif variant == "null-value":
            policy.append({"account_id": a, behaviour.policy_field: None})
        for _ in range(rng.randint(1, 3)):
            k += 1
            observed = rng.choice([True, False, None]) if behaviour.log_field == "mfa_used" else rng.choice(["password", "token", "certificate", None])
            e = _ev(sc, k, fmt_ts(BASE + rng.randint(0, 100_000) * US + rng.choice([0, 0, 137]), rng), a,
                    rng.choice(["login_success", "login_success", "login_failure"]), f"{sc}-h")
            e[behaviour.log_field] = observed
            events.append(e)
    return Scenario(sc, behaviour.id, _inject_noise(events, sc, rng, ["account_id"], tags), policy, tags)


def gen_scenarios(behaviour_id: str, n: int, seed: int, configs: list[tuple[int, int]] | None = None):
    """-> {(window_s, threshold): [Scenario, ...]} - one Spark run per config, all scenarios for it inside."""
    beh = B.BEHAVIOURS[behaviour_id]
    rng = random.Random(f"{seed}:{behaviour_id}")
    if beh.recipe == B.POLICY_COMPARE:
        return {(0, 0): [gen_policy(rng, i, beh) for i in range(n)]}
    configs = configs or [(w, t) for w in rng.sample(WINDOWS, 3) for t in rng.sample(THRESHOLDS, 1)]
    out: dict = {c: [] for c in configs}
    for i in range(n):
        w, t = configs[i % len(configs)]
        gen = gen_sequence if beh.recipe == B.SEQUENCE_THEN_TRIGGER else gen_distinct
        out[(w, t)].append(gen(rng, i, w, t, beh))
    return out
