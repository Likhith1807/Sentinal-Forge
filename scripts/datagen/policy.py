"""Deterministic synthetic account-policy assignment.

No public dataset provides an account policy (see docs/data-sources.md), so
policy is synthetic. Assignment is a pure function of ``(seed, account_id)``
so the synthetic background generator and the LANL mapper agree on it, and a
policy can be re-derived without storing state.

Background events are compliant with this policy by construction: successes
use ``expected_auth_method`` and set ``mfa_used == mfa_required``. That is
what guarantees the background cannot trigger B4/B5.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

import numpy as np

AUTH_METHODS = ("password", "token", "certificate")

SERVICE_FRACTION = 0.10
HUMAN_MFA_FRACTION = 0.60


def _unit_interval(seed: int, account_id: str, salt: str) -> float:
    digest = hashlib.blake2b(f"{seed}|{salt}|{account_id}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def assign(account_ids: Iterable[str], seed: int):
    """Return ``(is_service, expected_method_code, mfa_required)`` numpy arrays.

    Humans: password (70%) or token (30%); MFA required for 60%.
    Service accounts: password / token / certificate (1/3 each); never MFA.
    """
    ids = list(account_ids)
    n = len(ids)
    is_service = np.zeros(n, dtype=bool)
    method = np.zeros(n, dtype=np.int8)
    mfa = np.zeros(n, dtype=bool)
    for i, account_id in enumerate(ids):
        if _unit_interval(seed, account_id, "type") < SERVICE_FRACTION:
            is_service[i] = True
            method[i] = min(int(_unit_interval(seed, account_id, "svc-method") * 3), 2)
        else:
            method[i] = 0 if _unit_interval(seed, account_id, "method") < 0.70 else 1
            mfa[i] = _unit_interval(seed, account_id, "mfa") < HUMAN_MFA_FRACTION
    return is_service, method, mfa


def to_records(account_ids: Iterable[str], seed: int) -> list[dict]:
    ids = list(account_ids)
    is_service, method, mfa = assign(ids, seed)
    return [
        {
            "account_id": account_id,
            "account_type": "service" if is_service[i] else "human",
            "expected_auth_method": AUTH_METHODS[method[i]],
            "mfa_required": bool(mfa[i]),
        }
        for i, account_id in enumerate(ids)
    ]
