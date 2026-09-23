"""Datasets: a named data source with immutable VERSIONS, each carrying a column/type profile.

A schema change is modelled as what it really is - a new version of the same source with a different
profile - so "which rules does this affect?" is a comparison of two stored profiles, and every run records
the exact (dataset, version, fingerprint) it read.

Event files are JSON Lines; the policy reference is `{"records": [...]}` or JSON Lines. The Spark engine
also reads Parquet, but profiles for Parquet are supplied by the caller (see scripts/bench).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from ..validation import dataset_profile_from_columns
from .settings import Settings
from .store import Store, dumps, new_id, now

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")
SAMPLE_ROWS = 20000


class DatasetError(ValueError):
    pass


def _type_of(values: list, column: str) -> str:
    kinds = set()
    for v in values:
        if v is None:
            continue
        kinds.add("boolean" if isinstance(v, bool) else "long" if isinstance(v, int) else "double" if isinstance(v, float)
                  else "string" if isinstance(v, str) else "struct")
    if not kinds:
        return "null"
    if kinds == {"string"}:
        if column == "timestamp" and all(isinstance(v, str) and TS_RE.match(v) for v in values if v is not None):
            return "timestamp"
        return "string"
    return kinds.pop() if len(kinds) == 1 else "mixed"


def read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{path.name} line {n}: not valid JSON ({exc.msg})") from None
            if not isinstance(obj, dict):
                raise DatasetError(f"{path.name} line {n}: each line must be a JSON object")
            rows.append(obj)
            if limit and len(rows) >= limit:
                break
    return rows


def read_policy(path: Path | None) -> list[dict]:
    if path is None or not Path(path).exists():
        return []
    text = Path(path).read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
        return doc["records"] if isinstance(doc, dict) else doc
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def infer_profile(events_path: Path, policy_path: Path | None) -> tuple[dict, int]:
    rows = read_jsonl(events_path)
    cols: dict = {}
    for r in rows[:SAMPLE_ROWS]:
        for k in r:
            cols.setdefault(k, [])
    for r in rows[:SAMPLE_ROWS]:
        for k in cols:
            cols[k].append(r.get(k))
    log = {k: _type_of(v, k) for k, v in cols.items()}
    pol_rows = read_policy(policy_path)
    pcols: dict = {}
    for r in pol_rows:
        for k in r:
            pcols.setdefault(k, [])
    for r in pol_rows:
        for k in pcols:
            pcols[k].append(r.get(k))
    return dataset_profile_from_columns(log, {k: _type_of(v, k) for k, v in pcols.items()}), len(rows)


def fingerprint(*paths: Path | None) -> str:
    h = hashlib.sha256()
    for p in paths:
        if p and Path(p).exists():
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _version_dir(settings: Settings, dataset_id: str, version: int) -> Path:
    d = settings.datasets_dir / dataset_id / f"v{version}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def register(store: Store, settings: Settings, name: str, kind: str, events_src: Path, policy_src: Path | None,
             description: str = "", dataset_id: str | None = None) -> dict:
    if kind not in ("demonstration", "uploaded", "synthetic-scale"):
        raise DatasetError(f"unknown dataset kind {kind!r}")
    dataset_id = dataset_id or new_id()
    d = _version_dir(settings, dataset_id, 1)
    events_path = d / "auth_events.jsonl"
    shutil.copyfile(events_src, events_path)
    policy_path = None
    if policy_src:
        policy_path = d / "account_policy.json"
        shutil.copyfile(policy_src, policy_path)
    profile, rows = infer_profile(events_path, policy_path)
    with store.tx() as c:
        c.execute("INSERT INTO datasets(id, name, kind, description, current_version, created_at) VALUES (?,?,?,?,?,?)",
                  (dataset_id, name, kind, description, 1, now()))
        c.execute("INSERT INTO dataset_versions VALUES (?,?,?,?,?,?,?,?,?)",
                  (dataset_id, 1, str(events_path), str(policy_path) if policy_path else None, dumps(profile), None,
                   fingerprint(events_path, policy_path), rows, now()))
    return get(store, dataset_id)


def get(store: Store, dataset_id: str, version: int | None = None) -> dict:
    ds = store.one("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
    if ds is None:
        raise KeyError(dataset_id)
    v = version or ds["current_version"]
    ver = store.one("SELECT * FROM dataset_versions WHERE dataset_id = ? AND version = ?", (dataset_id, v))
    if ver is None:
        raise KeyError(f"{dataset_id} v{v}")
    return {"id": ds["id"], "name": ds["name"], "kind": ds["kind"], "description": ds["description"],
            "currentVersion": ds["current_version"], "version": ver["version"], "eventsPath": ver["events_path"],
            "policyPath": ver["policy_path"], "profile": json.loads(ver["profile"]),
            "change": json.loads(ver["change"]) if ver["change"] else None, "fingerprint": ver["fingerprint"],
            "rowCount": ver["row_count"], "createdAt": ver["created_at"]}


def list_all(store: Store) -> list[dict]:
    return [get(store, r["id"]) for r in store.q("SELECT id FROM datasets ORDER BY created_at")]


def versions(store: Store, dataset_id: str) -> list[dict]:
    return [get(store, dataset_id, r["version"]) for r in store.q(
        "SELECT version FROM dataset_versions WHERE dataset_id = ? ORDER BY version", (dataset_id,))]


def _retype(value, to: str):
    if value is None:
        return None
    if to == "string":
        return ("true" if value else "false") if isinstance(value, bool) else str(value)
    if to == "long":
        return int(value) if not isinstance(value, str) else (int(value) if value.lstrip("-").isdigit() else 0)
    raise DatasetError(f"cannot retype to {to!r}")


def derive_version(store: Store, settings: Settings, dataset_id: str, drop: list[str] | None = None,
                   retype: dict | None = None, restored_from: int | None = None) -> dict:
    """A new immutable version of `dataset_id` with columns removed / retyped (or a copy of an earlier version)."""
    cur = get(store, dataset_id)
    if cur["kind"] not in ("demonstration", "uploaded"):
        raise DatasetError("schema changes can only be simulated on demonstration or uploaded datasets")
    drop, retype = drop or [], retype or {}
    new_version = cur["currentVersion"] + 1
    d = _version_dir(settings, dataset_id, new_version)
    if restored_from is not None:
        src = get(store, dataset_id, restored_from)
        shutil.copyfile(src["eventsPath"], d / "auth_events.jsonl")
        if src["policyPath"]:
            shutil.copyfile(src["policyPath"], d / "account_policy.json")
        change = {"restoredFrom": restored_from}
    else:
        events = read_jsonl(Path(cur["eventsPath"]))
        log_drop = {c for c in drop if not c.startswith("policy.")}
        pol_drop = {c.removeprefix("policy.") for c in drop if c.startswith("policy.")}
        log_ret = {c: t for c, t in retype.items() if not c.startswith("policy.")}
        pol_ret = {c.removeprefix("policy."): t for c, t in retype.items() if c.startswith("policy.")}
        bad = [c for c in log_drop | set(log_ret) if c not in cur["profile"]["columns"]] + \
              [f"policy.{c}" for c in pol_drop | set(pol_ret) if c not in cur["profile"]["policyColumns"]]
        if bad:
            raise DatasetError(f"no such column(s): {', '.join(sorted(bad))}")
        out = []
        for r in events:
            r = {k: (_retype(v, log_ret[k]) if k in log_ret else v) for k, v in r.items() if k not in log_drop}
            out.append(r)
        (d / "auth_events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8", newline="\n")
        if cur["policyPath"]:
            pol = [{k: (_retype(v, pol_ret[k]) if k in pol_ret else v) for k, v in r.items() if k not in pol_drop}
                   for r in read_policy(Path(cur["policyPath"]))]
            (d / "account_policy.json").write_text(json.dumps({"records": pol}, indent=1), encoding="utf-8", newline="\n")
        change = {"drop": drop, "retype": retype}
    events_path = d / "auth_events.jsonl"
    policy_path = d / "account_policy.json" if (d / "account_policy.json").exists() else None
    profile, rows = infer_profile(events_path, policy_path)
    with store.tx() as c:
        c.execute("INSERT INTO dataset_versions VALUES (?,?,?,?,?,?,?,?,?)",
                  (dataset_id, new_version, str(events_path), str(policy_path) if policy_path else None, dumps(profile),
                   dumps(change), fingerprint(events_path, policy_path), rows, now()))
        c.execute("UPDATE datasets SET current_version = ? WHERE id = ?", (new_version, dataset_id))
    return get(store, dataset_id)
