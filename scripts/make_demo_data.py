"""Generate the bundled demonstration dataset (deterministic; committed under data/demo/).

    python scripts/make_demo_data.py

Two days of authentication events for 46 accounts, plus GRADED planted incidents - failure counts,
distinct-account counts, distinct-host counts and time spreads all vary - so that changing one number in a
report ("5 or more" -> "8 or more", "10 minutes" -> "2 minutes") really does change which incidents a
compiled rule catches. Background traffic is deliberately quiet (no bursts) so that only planted
incidents can fire; `incidents.json` lists every one with what a correct rule must find.

It also plants realistic data-quality problems (redelivered events, malformed timestamps, an account with
no policy row, a null MFA flag) so the run pages have something true to report.

This is SYNTHETIC data and the UI labels it as demonstration data.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "demo"
DAY0 = dt.datetime(2026, 3, 2, tzinfo=dt.timezone.utc)
rng = random.Random(20260302)

HUMANS = [f"{n}{i}" for i, n in enumerate(
    ["amir", "bianca", "carlos", "dana", "elif", "farid", "grace", "hiro", "ines", "jamal", "kavya", "liam", "mei",
     "nadia", "omar", "priya", "quinn", "rosa", "sanjay", "tara", "uma", "viktor", "wen", "ximena", "yusuf", "zoe",
     "aaron", "bella", "cyrus", "dora", "ethan", "fatima", "gus", "hana", "ivan", "jia", "kofi", "lena", "marco", "nina"])]
SERVICES = [f"svc-{n}" for n in ["backup", "etl", "report", "sync", "monitor", "deploy"]]
HOSTS = [f"{p}-{i:02d}" for p in ["WIN-LAB", "BASTION", "SSO-GW", "RDP-EDGE", "JUMP"] for i in range(1, 7)]

policy = []
for a in HUMANS:
    policy.append({"account_id": a, "account_type": "human",
                   "expected_auth_method": rng.choice(["password", "password", "token"]), "mfa_required": rng.random() < 0.8})
for a in SERVICES:
    policy.append({"account_id": a, "account_type": "service",
                   "expected_auth_method": rng.choice(["certificate", "token"]), "mfa_required": False})
pol = {p["account_id"]: p for p in policy}

events: list[dict] = []
n_ev = 0


def ts(seconds: float) -> str:
    t = DAY0 + dt.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def emit(seconds, account, etype, host, method=None, mfa=None, **kw):
    global n_ev
    n_ev += 1
    p = pol.get(account)
    method = method if method is not None else (p["expected_auth_method"] if p else "password")
    mfa = mfa if mfa is not None else bool(p and p["mfa_required"])
    e = {"event_id": f"d{n_ev:05d}", "timestamp": ts(seconds), "account_id": account, "event_type": etype,
         "source_host": host, "source_ip": None, "auth_method": method, "mfa_used": mfa,
         "session_id": f"sess-{n_ev:05d}" if etype == "login_success" else None}
    e.update(kw)
    events.append(e)
    return e


# ---- quiet background: each account logs in from its own home host a few times a day; rare isolated typos
home = {a: rng.choice(HOSTS) for a in HUMANS + SERVICES}
for a in HUMANS + SERVICES:
    for day in (0, 1):
        for _ in range(rng.randint(4, 7)):
            emit(day * 86400 + rng.randint(7 * 3600, 19 * 3600), a, "login_success", home[a])
        if rng.random() < 0.5:                                   # one typo, then a success ~ an hour later
            t = day * 86400 + rng.randint(8 * 3600, 16 * 3600)
            emit(t, a, "login_failure", home[a])

incidents: list[dict] = []
BASE_T = 3 * 3600 + 17 * 60                                      # incidents start at 03:17 and are spread over the two days


def plant(kind, label, params, expect, event_ids):
    incidents.append({"kind": kind, "label": label, "params": params, "expect": expect, "eventIds": event_ids})


victims = iter(HUMANS[:24])

# ---- B1: repeated failures then a success. (failures, spread seconds)
for k, spread in [(3, 20), (4, 45), (5, 30), (6, 90), (8, 120), (10, 240), (12, 400), (15, 55)]:
    a, h = next(victims), rng.choice(HOSTS)
    t0 = BASE_T + len(incidents) * 5400
    ids = [emit(t0 + i * spread / max(1, k - 1), a, "login_failure", h)["event_id"] for i in range(k)]
    ids.append(emit(t0 + spread + 3, a, "login_success", h)["event_id"])
    plant("repeated-failed-login-then-success", f"{k} failures over {spread}s then success", {"failures": k, "spreadSeconds": spread, "account": a},
          {"minFailuresAtLeast": k, "windowAtLeastSeconds": spread + 3}, ids)

# ---- B2: many accounts failing from one host. (distinct accounts, spread seconds)
sprayed = itertools.cycle(HUMANS[24:] + HUMANS[:24])   # a spray may reuse accounts across separate incidents
for d, spread in [(2, 40), (3, 90), (4, 300), (5, 200), (6, 480), (8, 600), (10, 900), (12, 1100)]:
    h = f"ATTACKER-{len(incidents):02d}"
    t0 = BASE_T + len(incidents) * 5400
    ids = [emit(t0 + i * spread / max(1, d - 1), next(sprayed), "login_failure", h)["event_id"] for i in range(d)]
    plant("password-spray-across-accounts", f"{d} accounts from one host over {spread}s", {"distinctAccounts": d, "spreadSeconds": spread, "host": h},
          {"minDistinctAccountsAtLeast": d, "windowAtLeastSeconds": spread}, ids)

# ---- B3: one account authenticating from several hosts. (distinct hosts, spread seconds)
for d, spread in [(2, 90), (2, 900), (3, 300), (3, 1500), (4, 1200), (5, 2400)]:
    a = HUMANS[(len(incidents) * 3) % len(HUMANS)]
    t0 = BASE_T + len(incidents) * 5400
    hs = rng.sample(HOSTS, d)
    ids = [emit(t0 + i * spread / max(1, d - 1), a, "login_success", hs[i])["event_id"] for i in range(d)]
    plant("multi-host-authentication", f"{d} hosts over {spread}s", {"distinctHosts": d, "spreadSeconds": spread, "account": a},
          {"minDistinctHostsAtLeast": d, "windowAtLeastSeconds": spread}, ids)

# ---- B4: authentication method differs from the expected method
for a in HUMANS[10:16]:
    wrong = next(m for m in ["password", "token", "certificate"] if m != pol[a]["expected_auth_method"])
    e = emit(BASE_T + len(incidents) * 5400, a, "login_success", home[a], method=wrong)
    plant("auth-method-policy-violation", f"{a} used {wrong}, expected {pol[a]['expected_auth_method']}", {"account": a}, {"alerts": 1}, [e["event_id"]])

# ---- B5: MFA not used on an MFA-required account
required = [a for a in HUMANS if pol[a]["mfa_required"]]
for a in required[3:9]:
    e = emit(BASE_T + len(incidents) * 5400, a, "login_success", home[a], mfa=False)
    plant("mfa-missing-on-required-account", f"{a} logged in without MFA", {"account": a}, {"alerts": 1}, [e["event_id"]])

# ---- realistic data-quality problems (all real rows the pipeline must handle, not annotations)
emit(BASE_T + 100, "contractor-x9", "login_success", "JUMP-03")                     # account with no policy row -> insufficient_context
e = emit(BASE_T + 200, HUMANS[5], "login_success", home[HUMANS[5]])
e["mfa_used"] = None                                                                # unobserved MFA flag
dupes = [dict(events[i]) for i in (10, 25, 40)]                                      # collector redelivered three events
events.extend(dupes)
for bad in ("2026-03-02 09:15:00", "not-a-timestamp"):                               # rows with unparseable timestamps
    n_ev += 1
    events.append({"event_id": f"d{n_ev:05d}", "timestamp": bad, "account_id": HUMANS[0], "event_type": "login_success",
                   "source_host": home[HUMANS[0]], "source_ip": None, "auth_method": pol[HUMANS[0]]["expected_auth_method"],
                   "mfa_used": True, "session_id": None})

rng.shuffle(events)                                                                  # collectors do not deliver in order
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "auth_events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8", newline="\n")
(OUT / "account_policy.json").write_text(json.dumps({"records": policy}, indent=1), encoding="utf-8", newline="\n")
(OUT / "incidents.json").write_text(json.dumps({
    "description": "Planted incidents in auth_events.jsonl. `expect` is what a correct rule must find; window/threshold "
                   "values in a report decide which of these a compiled rule can catch.",
    "synthetic": True, "seed": 20260302, "events": len(events), "incidents": incidents}, indent=1), encoding="utf-8", newline="\n")
print(f"{len(events)} events, {len(policy)} policy rows, {len(incidents)} planted incidents -> {OUT}")
