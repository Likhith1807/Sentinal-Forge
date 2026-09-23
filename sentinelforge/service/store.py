"""SQLite persistence: one file, WAL mode, explicit transactions.

Concurrency model (what "concurrency-safe decision storage" means here): every write runs inside
`BEGIN IMMEDIATE`, so writers serialise in the database rather than in Python; state transitions are
compare-and-set (`UPDATE ... WHERE state IN (...)` and a rowcount check), so two analysts approving the
same rule version, or a resume racing a schema change, cannot both win; every row has a UUID key so nothing
is ever overwritten by a same-named file. Rows are never deleted by the application - superseded or failed
things keep their history.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, description TEXT, current_version INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dataset_versions(
  dataset_id TEXT NOT NULL, version INTEGER NOT NULL, events_path TEXT NOT NULL, policy_path TEXT, profile TEXT NOT NULL,
  change TEXT, fingerprint TEXT NOT NULL, row_count INTEGER, created_at TEXT NOT NULL, PRIMARY KEY(dataset_id, version));
CREATE TABLE IF NOT EXISTS reports(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL, text_sha256 TEXT NOT NULL, source TEXT NOT NULL,
  created_by TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analyses(
  id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id), extractor TEXT NOT NULL, extractor_meta TEXT,
  dataset_id TEXT, dataset_version INTEGER, status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rules(
  id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id), behaviour_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rule_versions(
  id TEXT PRIMARY KEY, rule_id TEXT NOT NULL REFERENCES rules(id), version INTEGER NOT NULL, analysis_id TEXT NOT NULL REFERENCES analyses(id),
  compiled TEXT NOT NULL, rule_hash TEXT NOT NULL, compiler_version TEXT NOT NULL, origin TEXT NOT NULL, overrides TEXT,
  state TEXT NOT NULL, previous_state TEXT, pause_reason TEXT, created_by TEXT, created_at TEXT NOT NULL, UNIQUE(rule_id, version));
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, ref TEXT NOT NULL, detail TEXT, history TEXT NOT NULL, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, rule_version_id TEXT NOT NULL REFERENCES rule_versions(id), dataset_id TEXT NOT NULL, dataset_version INTEGER NOT NULL,
  dataset_fingerprint TEXT NOT NULL, purpose TEXT NOT NULL, engine TEXT NOT NULL, state TEXT NOT NULL, run_dir TEXT, summary TEXT, error TEXT,
  created_by TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS decisions(
  id TEXT PRIMARY KEY, rule_version_id TEXT NOT NULL REFERENCES rule_versions(id), run_id TEXT, decision TEXT NOT NULL, analyst TEXT NOT NULL,
  note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_events(
  id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, from_version INTEGER, to_version INTEGER NOT NULL, change TEXT NOT NULL, impact TEXT NOT NULL,
  created_by TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_runs_rule ON runs(rule_version_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return str(uuid.uuid4())


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def loads(text):
    return json.loads(text) if text else None


class Conflict(Exception):
    """A compare-and-set lost: the row was not in a state that allows the transition."""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        conn = self._conn()
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    @contextmanager
    def tx(self):
        """A write transaction. Serialises against other writers (IMMEDIATE) and rolls back on any exception."""
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, args).fetchone()

    def cas(self, c: sqlite3.Connection, table: str, row_id: str, new_state: str, allowed: tuple, extra: dict | None = None) -> None:
        """state -> new_state only if the current state is in `allowed`; raises Conflict otherwise."""
        sets = ["state = ?"] + [f"{k} = ?" for k in (extra or {})]
        args = [new_state, *(extra or {}).values(), row_id, *allowed]
        cur = c.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ? AND state IN ({','.join('?' * len(allowed))})", args)
        if cur.rowcount != 1:
            raise Conflict(f"{table} {row_id} is not in one of {allowed}")


def row_dict(row: sqlite3.Row | None, json_cols: tuple = ()) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in json_cols:
        if k in d:
            d[k] = loads(d[k])
    return d
