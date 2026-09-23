"""Background jobs with explicit, persisted states.

  analyze:  queued -> extracting -> validating -> ready | needs_review | rejected | failed
  run:      queued -> running    -> completed | failed

Every transition is appended to the job's `history` with a timestamp, so the UI can show exactly where a
job is and how long each stage took. Jobs survive restarts: anything found mid-flight at start-up is marked
`failed` with reason "interrupted" (never silently re-run, never left "running" forever).
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .store import Store, dumps, loads, new_id, now

TERMINAL = {"ready", "needs_review", "rejected", "completed", "failed"}
IN_FLIGHT = {"queued", "extracting", "validating", "running"}


class JobHandle:
    def __init__(self, runner: "JobRunner", job_id: str):
        self.runner, self.id = runner, job_id

    def set(self, state: str, note: str | None = None, error: dict | None = None) -> None:
        self.runner.transition(self.id, state, note, error)


class JobRunner:
    def __init__(self, store: Store, workers: int = 2):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="sf-job")
        self._futures: dict[str, object] = {}
        self._lock = threading.Lock()

    def recover(self) -> int:
        """Mark jobs left in flight by a previous process as failed('interrupted')."""
        n = 0
        with self.store.tx() as c:
            for row in c.execute(f"SELECT id, history FROM jobs WHERE state IN ({','.join('?' * len(IN_FLIGHT))})", tuple(IN_FLIGHT)).fetchall():
                hist = loads(row["history"]) or []
                hist.append({"state": "failed", "at": now(), "note": "interrupted: the service restarted while this job was in flight"})
                c.execute("UPDATE jobs SET state='failed', history=?, error=?, updated_at=? WHERE id=?",
                          (dumps(hist), dumps({"kind": "interrupted", "message": "the service restarted while this job was running"}), now(), row["id"]))
                n += 1
        return n

    def submit(self, kind: str, ref: str, fn: Callable[[JobHandle], None], detail: dict | None = None) -> str:
        job_id = new_id()
        with self.store.tx() as c:
            c.execute("INSERT INTO jobs(id, kind, state, ref, detail, history, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      (job_id, kind, "queued", ref, dumps(detail or {}), dumps([{"state": "queued", "at": now()}]), now(), now()))
        handle = JobHandle(self, job_id)

        def wrapper():
            try:
                fn(handle)
            except BaseException as exc:  # noqa: BLE001 - a crashing job becomes a failed job, with its traceback kept
                self.transition(job_id, "failed", "unhandled error", {"kind": type(exc).__name__, "message": str(exc),
                                                                       "trace": traceback.format_exc()[-3000:]})
        with self._lock:
            self._futures[job_id] = self.pool.submit(wrapper)
        return job_id

    def transition(self, job_id: str, state: str, note: str | None = None, error: dict | None = None) -> None:
        with self.store.tx() as c:
            row = c.execute("SELECT history, state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["state"] in TERMINAL:
                return                                             # terminal states are final
            hist = loads(row["history"]) or []
            hist.append({"state": state, "at": now(), **({"note": note} if note else {})})
            c.execute("UPDATE jobs SET state=?, history=?, error=?, updated_at=? WHERE id=?",
                      (state, dumps(hist), dumps(error) if error else None, now(), job_id))

    def get(self, job_id: str) -> dict | None:
        r = self.store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if r is None:
            return None
        return {"id": r["id"], "kind": r["kind"], "state": r["state"], "ref": r["ref"], "detail": loads(r["detail"]),
                "history": loads(r["history"]), "error": loads(r["error"]), "createdAt": r["created_at"], "updatedAt": r["updated_at"]}

    def wait(self, job_id: str, timeout: float = 120.0) -> dict:
        fut = self._futures.get(job_id)
        if fut is not None:
            fut.result(timeout=timeout)
        return self.get(job_id)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=False)
