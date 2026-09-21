"""Vectorised synthetic benign background events.

Every construction choice exists to make the background *provably unable to
trigger any of the five rules*, so that every alert on a generated dataset
is either an injected, labelled incident or a genuine bug:

* Time is divided into 300-second slots. An account logs in at most once
  per slot, at second 60..299 of the slot, and any failure preceding a login
  falls at second 5..54 of that same slot. Failures per login are 0-2, so at
  most 4 failures can fall inside any 120s window (B1 needs 5).
* Each account has exactly one home host, and a host serves at most two
  accounts. So an account never succeeds from two hosts (B3) and at most two
  distinct accounts fail from one host (B2 needs 4).
* Successes use the account's policy method and ``mfa_used == mfa_required``
  (B4/B5 never fire); every account has a policy row.

The constraints are re-verified independently by ``refdetect`` in the tests,
not just asserted here.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import policy as policy_mod

SLOT_SECONDS = 300
SLOTS_PER_DAY = 86400 // SLOT_SECONDS          # 288
HUMAN_SLOTS = np.arange(96, 216)               # 08:00-18:00
ACCOUNTS_PER_HOST = 2
ACCOUNT_BLOCK = 20_000                          # accounts processed per vectorised block
FAILURE_PROBS = (0.90, 0.08, 0.02)             # P(0, 1, 2 failures before a login)

SCHEMA_COLUMNS = [
    "event_id", "timestamp", "account_id", "event_type",
    "source_host", "source_ip", "auth_method", "mfa_used", "session_id",
]


@dataclass(frozen=True)
class BackgroundConfig:
    n_accounts: int = 20_000
    start: dt.date = dt.date(2026, 1, 5)
    days: int = 7
    seed: int = 42
    human_logins_per_day: float = 3.0
    service_logins_per_day: float = 12.0


class Background:
    """Holds the per-account identity/policy arrays shared across days."""

    def __init__(self, cfg: BackgroundConfig):
        self.cfg = cfg
        n = cfg.n_accounts
        self.account_ids = [f"u{i:08d}" for i in range(n)]
        n_hosts = -(-n // ACCOUNTS_PER_HOST)
        self.host_ids = [f"h{i:07d}" for i in range(n_hosts)]
        self.is_service, self.method_code, self.mfa_required = policy_mod.assign(self.account_ids, cfg.seed)
        self._account_arr = pa.array(self.account_ids, pa.string())
        self._host_arr = pa.array(self.host_ids, pa.string())
        self._ip_arr = pa.array(
            [f"10.{(i >> 16) & 255}.{(i >> 8) & 255}.{i & 255}" for i in range(n_hosts)], pa.string()
        )
        self._method_names = np.array(policy_mod.AUTH_METHODS)

    def iter_policy_records(self):
        """Yield one policy record per background account (streamed; may be millions)."""
        for i, account_id in enumerate(self.account_ids):
            yield {
                "account_id": account_id,
                "account_type": "service" if self.is_service[i] else "human",
                "expected_auth_method": policy_mod.AUTH_METHODS[self.method_code[i]],
                "mfa_required": bool(self.mfa_required[i]),
            }

    def day_date(self, day_index: int) -> dt.date:
        return self.cfg.start + dt.timedelta(days=day_index)

    def generate_day(self, day_index: int, counter_start: int) -> tuple[pa.Table, int]:
        """Return ``(table, next_counter)`` for one calendar day, sorted by time."""
        cfg = self.cfg
        rng = np.random.default_rng([cfg.seed, 7, day_index])
        n = cfg.n_accounts

        acct_parts, sec_parts, ms_parts, success_parts = [], [], [], []
        for block_start in range(0, n, ACCOUNT_BLOCK):
            a = np.arange(block_start, min(block_start + ACCOUNT_BLOCK, n))
            lam = np.where(self.is_service[a], cfg.service_logins_per_day, cfg.human_logins_per_day)
            m = np.minimum(rng.poisson(lam), SLOTS_PER_DAY)
            human = ~self.is_service[a]
            m = np.where(human, np.minimum(m, len(HUMAN_SLOTS)), m)

            # Choose m distinct slots per account: rank slots by random keys and
            # push slots humans never use to the end so they are never selected.
            keys = rng.random((len(a), SLOTS_PER_DAY))
            outside = np.ones(SLOTS_PER_DAY, dtype=bool)
            outside[HUMAN_SLOTS] = False
            keys[np.ix_(human, outside)] += 2.0
            order = np.argsort(keys, axis=1)
            chosen = np.arange(SLOTS_PER_DAY)[None, :] < m[:, None]
            slot = order[chosen]
            acct = np.repeat(a, m)

            login_sec = slot * SLOT_SECONDS + rng.integers(60, SLOT_SECONDS, size=len(slot))
            login_ms = rng.integers(0, 1000, size=len(slot))

            k = rng.choice(3, size=len(slot), p=FAILURE_PROBS)
            fail_acct = np.repeat(acct, k)
            fail_slot = np.repeat(slot, k)
            fail_sec = fail_slot * SLOT_SECONDS + rng.integers(5, 55, size=len(fail_slot))
            fail_ms = rng.integers(0, 1000, size=len(fail_slot))

            acct_parts += [acct, fail_acct]
            sec_parts += [login_sec, fail_sec]
            ms_parts += [login_ms, fail_ms]
            success_parts += [np.ones(len(acct), dtype=bool), np.zeros(len(fail_acct), dtype=bool)]

        acct_idx = np.concatenate(acct_parts)
        sec = np.concatenate(sec_parts)
        ms = np.concatenate(ms_parts)
        is_success = np.concatenate(success_parts)

        order = np.argsort(sec * 1000 + ms, kind="stable")
        acct_idx, sec, ms, is_success = acct_idx[order], sec[order], ms[order], is_success[order]
        count = len(acct_idx)
        if count == 0:
            return pa.table({c: pa.array([], pa.string()) for c in SCHEMA_COLUMNS}), counter_start

        day_epoch = int(dt.datetime.combine(self.day_date(day_index), dt.time(), dt.timezone.utc).timestamp())
        table = self._assemble(acct_idx, day_epoch + sec, ms, is_success, rng, counter_start, "b")
        return table, counter_start + count

    def _assemble(self, acct_idx, epoch_sec, ms, is_success, rng, counter_start, id_prefix) -> pa.Table:
        count = len(acct_idx)
        host_idx = acct_idx // ACCOUNTS_PER_HOST
        counters = pc.utf8_lpad(pa.array(np.arange(counter_start, counter_start + count)).cast(pa.string()), 12, "0")
        event_id = pc.binary_join_element_wise(pa.scalar(id_prefix), counters, "")

        ts_seconds = pc.strftime(
            pa.array(epoch_sec.astype("datetime64[s]")), format="%Y-%m-%dT%H:%M:%S"
        )
        ms_str = pc.utf8_lpad(pa.array(ms).cast(pa.string()), 3, "0")
        timestamp = pc.binary_join_element_wise(
            pc.binary_join_element_wise(ts_seconds, ms_str, "."), pa.scalar("Z"), ""
        )

        event_type = pa.array(np.where(is_success, "login_success", "login_failure"))
        auth_method = pa.array(self._method_names[self.method_code[acct_idx]])
        mfa_used = pa.array(is_success & self.mfa_required[acct_idx])

        ip_populated = pa.array(rng.random(count) < 0.30)
        source_ip = pc.if_else(ip_populated, self._ip_arr.take(pa.array(host_idx)),
                               pa.scalar(None, pa.string()))
        session = pc.binary_join_element_wise(pa.scalar("s"), counters, "")
        session_id = pc.if_else(pa.array(is_success), session, pa.scalar(None, pa.string()))

        return pa.table({
            "event_id": event_id,
            "timestamp": timestamp,
            "account_id": self._account_arr.take(pa.array(acct_idx)),
            "event_type": event_type,
            "source_host": self._host_arr.take(pa.array(host_idx)),
            "source_ip": source_ip,
            "auth_method": auth_method,
            "mfa_used": mfa_used,
            "session_id": session_id,
        })


def write_partition(table: pa.Table, events_root: Path, event_date: str, part_name: str,
                    compression: str = "snappy") -> Path:
    """Write one file into ``events_root/event_date=<date>/`` (Hive layout)."""
    directory = events_root / f"event_date={event_date}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{part_name}.parquet"
    pq.write_table(table, path, compression=compression)
    return path
