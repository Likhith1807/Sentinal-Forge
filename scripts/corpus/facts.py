"""Fact sheets: the structured truth every report and its gold are rendered from."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .vocab import (B1, B2, B3, B4, B5, ANALYSTS, FIRST, HOST_PREFIX, LAST, NUMBER_WORDS, SERVICE_PREFIX,
                    THRESHOLD_KEY)

TECHNIQUE = {
    B1: "T1110 (Brute Force), T1078 (Valid Accounts)",
    B2: "T1110.003 (Password Spraying)",
    B3: "T1078 (Valid Accounts)",
    B4: "T1078.003 (Valid Accounts: Local Accounts)",
    B5: "T1556 (Modify Authentication Process), T1621 (MFA Request Generation)",
}
REQUIRED = {
    B1: ["account_id", "event_type", "timestamp"],
    B2: ["account_id", "event_type", "timestamp", "source_host"],
    B3: ["account_id", "event_type", "timestamp", "source_host"],
    B4: ["account_id", "event_type", "auth_method"],
    B5: ["account_id", "event_type", "mfa_used"],
}
POLICY = {B4: ["policy.expected_auth_method"], B5: ["policy.mfa_required"]}
# Fields a report may explicitly call incidental for that behaviour (never a required one).
EXCLUDABLE = {B1: ["source_ip", "auth_method"], B2: ["auth_method"], B3: ["source_ip", "auth_method"],
              B4: ["mfa_used"], B5: ["auth_method"]}

THRESHOLDS = {B1: [3, 4, 5, 5, 5, 6, 8, 10], B2: [3, 4, 4, 5, 6, 8], B3: [2, 2, 2, 3]}
WINDOWS = {
    B1: [(30, "seconds"), (60, "seconds"), (90, "seconds"), (1, "minutes"), (2, "minutes"), (2, "minutes"),
         (3, "minutes"), (5, "minutes"), (10, "minutes")],
    B2: [(5, "minutes"), (10, "minutes"), (10, "minutes"), (15, "minutes"), (30, "minutes"), (1, "hours")],
    B3: [(5, "minutes"), (10, "minutes"), (15, "minutes"), (15, "minutes"), (30, "minutes"), (1, "hours")],
}
THRESHOLD_FORMS = ["at-least", "or-more", "or-more", "plus", "no-fewer", "more-than"]
METHODS = ["password", "token", "certificate"]


@dataclass
class Facts:
    family_id: str
    behaviour: str | None            # None for unsupported families
    technique: str
    threshold: int | None = None
    threshold_key: str | None = None
    threshold_form: str = "at-least"
    window_amount: int | None = None
    window_unit: str | None = None   # gold unit: seconds | minutes | hours
    window_form: str = "spaced"      # spaced ("2 minutes") | hyphen ("2-minute")
    required_fields: list = field(default_factory=list)
    policy_fields: list = field(default_factory=list)
    excluded_fields: list = field(default_factory=list)
    field_mode: str = "code"         # code (`account_id`) | natural ("the username")
    num_mode: str = "digits"         # digits | words
    entities: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    supported: bool = True
    unsupported: dict = field(default_factory=dict)

    @property
    def threshold_shown(self) -> int | None:
        """The number that appears in the text ('more than K' shows K = threshold - 1)."""
        if self.threshold is None:
            return None
        return self.threshold - 1 if self.threshold_form == "more-than" else self.threshold


def _person(rng: random.Random) -> str:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    return rng.choice([f"{first[0]}{last}", f"{first}.{last}", f"{first}{rng.randint(2, 9)}"])


def _service(rng: random.Random) -> str:
    return f"{rng.choice(SERVICE_PREFIX)}{rng.randint(1, 30):02d}"


def _host(rng: random.Random) -> str:
    return f"{rng.choice(HOST_PREFIX)}-{rng.randint(1, 19):02d}"


def _common_meta(rng: random.Random) -> dict:
    return {"analyst": rng.choice(ANALYSTS), "ticket": f"INC-{rng.randint(20000, 98999)}",
            "hhmm": f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}", "severity": rng.choice(["low", "medium", "high"]),
            "earlier_logins": rng.randint(2, 9), "subnet": f"10.{rng.randint(1, 250)}.{rng.randint(1, 250)}.0/24"}


def sample(behaviour: str, family_no: int, rng: random.Random, *, words_prob: float = 0.35,
           natural_prob: float = 0.5, excluded_prob: float = 0.55) -> Facts:
    tag = f"B{[B1, B2, B3, B4, B5].index(behaviour) + 1}"
    f = Facts(family_id=f"F-{tag}-{family_no:03d}", behaviour=behaviour, technique=TECHNIQUE[behaviour],
              required_fields=list(REQUIRED[behaviour]), policy_fields=list(POLICY.get(behaviour, [])),
              field_mode="natural" if rng.random() < natural_prob else "code",
              num_mode="words" if rng.random() < words_prob else "digits", meta=_common_meta(rng))
    if rng.random() < excluded_prob:
        f.excluded_fields = [rng.choice(EXCLUDABLE[behaviour])]

    if behaviour in THRESHOLD_KEY:
        f.threshold = rng.choice(THRESHOLDS[behaviour])
        f.threshold_key = THRESHOLD_KEY[behaviour]
        f.threshold_form = rng.choice(THRESHOLD_FORMS)
        if f.threshold_form == "more-than" and f.threshold < 3:
            f.threshold_form = "at-least"        # "more than 1" reads unnaturally
        f.window_amount, f.window_unit = rng.choice(WINDOWS[behaviour])
        f.window_form = "hyphen" if rng.random() < 0.35 else "spaced"

    if behaviour == B1:
        f.entities = {"acct": _person(rng) if rng.random() < 0.7 else _service(rng), "gw": _host(rng)}
        f.observed = {"n": f.threshold + rng.randint(1, 5), "span_s": rng.choice([41, 47, 58, 73, 84, 96])}
    elif behaviour == B2:
        n = f.threshold + rng.randint(1, 4)
        f.entities = {"gw": _host(rng), "accts": [_person(rng) for _ in range(min(n, 6))]}
        f.observed = {"n": n, "span_min": rng.choice([4, 7, 9, 12, 14, 22])}
    elif behaviour == B3:
        h1, h2 = _host(rng), _host(rng)
        while h2 == h1:
            h2 = _host(rng)
        f.entities = {"acct": _person(rng), "h1": h1, "h2": h2}
        f.observed = {"gap_min": rng.choice([2, 3, 4, 6, 7, 8])}
    elif behaviour == B4:
        expected = rng.choice(METHODS)
        f.entities = {"acct": _service(rng), "gw": _host(rng)}
        f.observed = {"expected": expected, "used": rng.choice([m for m in METHODS if m != expected])}
    elif behaviour == B5:
        f.entities = {"acct": _person(rng), "host": _host(rng)}
        f.observed = {"auth": rng.choice(METHODS)}
    return f
