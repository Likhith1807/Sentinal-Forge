"""Map LANL cyber1 ``auth.txt`` events into the SENTINEL Forge authentication schema.

    python -m scripts.datagen.lanl_mapper --auth data/external/lanl/auth.txt.gz \\
        --out data/generated/lanl --redteam data/external/lanl/redteam.txt.gz

LANL columns (comma-delimited, ``?`` = missing):
    time, source user@domain, destination user@domain, source computer,
    destination computer, authentication type, logon type, orientation, success/failure

What is real and what is not (see docs/data-sources.md):
    real       account_id, source_host, event_type (success/failure), event ordering
    synthetic  timestamp (LANL epoch-seconds offset from a fixed anchor date),
               auth_method and mfa_used (LANL has neither; both are assigned from the
               deterministic synthetic policy so the background is B4/B5-compliant),
               the policy table itself
    dropped    destination computer, authentication type, logon type (no schema field)

ASSUMPTIONS about the raw file, taken from LANL's published description and NOT yet
verified against the real data (access pending): orientation ``LogOn`` marks logon events,
status values are ``Success``/``Fail``, machine accounts end in ``$``. Each is an explicit
option, unrecognised values raise instead of being guessed at, and the output manifest
records a full value profile of every categorical column so the assumptions can be checked
on the first real run.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv

from . import GENERATOR_VERSION, policy as policy_mod
from .background import SCHEMA_COLUMNS, write_partition

RAW_COLUMNS = ["time", "src_user", "dst_user", "src_comp", "dst_comp",
               "auth_type", "logon_type", "orientation", "status"]
STATUS_TO_EVENT_TYPE = {"Success": "login_success", "Fail": "login_failure"}
MISSING = "?"
PROFILED = ("orientation", "status", "auth_type", "logon_type")
DEFAULT_ANCHOR = "2026-01-05"


class UnknownValueError(ValueError):
    """Raised when the raw file contains a value the mapper was not told how to handle."""


def _anchor_epoch(anchor: str) -> int:
    day = dt.date.fromisoformat(anchor)
    return int(dt.datetime.combine(day, dt.time(), dt.timezone.utc).timestamp())


def _open_reader(path: Path, block_size: int):
    return pacsv.open_csv(
        pa.input_stream(str(path), compression="detect"),
        read_options=pacsv.ReadOptions(column_names=RAW_COLUMNS, block_size=block_size),
        parse_options=pacsv.ParseOptions(delimiter=","),
        convert_options=pacsv.ConvertOptions(column_types={c: pa.string() for c in RAW_COLUMNS}),
    )


def map_auth_file(auth_path: Path, out_dir: Path, *, anchor: str = DEFAULT_ANCHOR,
                  account_field: str = "source", orientations: tuple[str, ...] = ("LogOn",),
                  exclude_machine_accounts: bool = True, on_unknown_status: str = "error",
                  policy_seed: int = 42, block_size: int = 64 << 20,
                  compression: str = "snappy", max_rows: int | None = None) -> dict:
    """Stream ``auth_path`` into partitioned Parquet under ``out_dir/events``. Returns the manifest."""
    if account_field not in ("source", "destination"):
        raise ValueError("account_field must be 'source' or 'destination'")
    if on_unknown_status not in ("error", "drop"):
        raise ValueError("on_unknown_status must be 'error' or 'drop'")

    events_root = out_dir / "events"
    account_col = "src_user" if account_field == "source" else "dst_user"
    anchor_epoch = _anchor_epoch(anchor)
    method_names = np.array(policy_mod.AUTH_METHODS)

    profile = {c: collections.Counter() for c in PROFILED}
    dropped = collections.Counter()
    seen_accounts: set[str] = set()
    rows_in = rows_out = chunk_no = 0
    line_offset = 0

    reader = _open_reader(auth_path, block_size)
    for batch in reader:
        table = pa.Table.from_batches([batch])
        n = table.num_rows
        if max_rows is not None and rows_in >= max_rows:
            break
        if max_rows is not None and rows_in + n > max_rows:
            table = table.slice(0, max_rows - rows_in)
            n = table.num_rows
        rows_in += n
        row_numbers = np.arange(line_offset, line_offset + n, dtype=np.int64)   # 0-based source row
        line_offset += n

        for column in PROFILED:
            for entry in pc.value_counts(table[column]).to_pylist():
                profile[column][entry["values"]] += entry["counts"]

        keep = pc.is_in(table["orientation"], value_set=pa.array(list(orientations)))
        dropped["orientation_not_selected"] += int(n - pc.sum(keep.cast(pa.int64())).as_py())

        account = table[account_col].combine_chunks()
        host = table["src_comp"].combine_chunks()
        missing = pc.or_(pc.equal(account, MISSING), pc.equal(host, MISSING))
        keep_missing = pc.and_(keep, pc.invert(missing))
        dropped["missing_account_or_host"] += int(pc.sum(pc.and_(keep, missing).cast(pa.int64())).as_py() or 0)
        keep = keep_missing

        if exclude_machine_accounts:
            machine = pc.match_substring_regex(account, r"^[^@]*\$(@|$)")
            dropped["machine_account"] += int(pc.sum(pc.and_(keep, machine).cast(pa.int64())).as_py() or 0)
            keep = pc.and_(keep, pc.invert(machine))

        status = table["status"].combine_chunks()
        event_type = pc.if_else(pc.equal(status, "Success"), "login_success",
                                pc.if_else(pc.equal(status, "Fail"), "login_failure",
                                           pa.scalar(None, pa.string())))
        unknown = pc.and_(keep, pc.is_null(event_type))
        n_unknown = int(pc.sum(unknown.cast(pa.int64())).as_py() or 0)
        if n_unknown:
            if on_unknown_status == "error":
                sample = sorted({v for v in pc.filter(status, unknown).unique().to_pylist()})[:10]
                raise UnknownValueError(
                    f"{n_unknown:,} selected rows have a status other than {sorted(STATUS_TO_EVENT_TYPE)}: "
                    f"{sample}. Re-run with on_unknown_status='drop' only after checking the value profile.")
            dropped["unknown_status"] += n_unknown
            keep = pc.and_(keep, pc.invert(unknown))

        if not pc.any(keep).as_py():
            continue

        try:
            seconds = pc.cast(table["time"].combine_chunks(), pa.int64())
        except pa.ArrowInvalid as exc:
            raise UnknownValueError(f"non-integer value in the 'time' column: {exc}") from exc
        if pc.min(seconds).as_py() < 1:
            raise UnknownValueError("LANL time must start at 1; found a value below 1")

        idx = np.flatnonzero(keep.to_numpy(zero_copy_only=False))
        account_k = pc.take(account, pa.array(idx))
        host_k = pc.take(host, pa.array(idx))
        type_k = pc.take(event_type, pa.array(idx))
        sec_k = seconds.to_numpy()[idx]
        rownum_k = row_numbers[idx]
        is_success = pc.equal(type_k, "login_success").to_numpy(zero_copy_only=False)

        unique_accounts = pc.unique(account_k)
        ids = unique_accounts.to_pylist()
        seen_accounts.update(ids)
        _, method_code, mfa_required = policy_mod.assign(ids, policy_seed)
        position = pc.index_in(account_k, value_set=unique_accounts).to_numpy(zero_copy_only=False)

        epoch = anchor_epoch + (sec_k - 1)
        rn = pc.utf8_lpad(pa.array(rownum_k).cast(pa.string()), 12, "0")
        timestamp = pc.binary_join_element_wise(
            pc.strftime(pa.array(epoch.astype("datetime64[s]")), format="%Y-%m-%dT%H:%M:%S"),
            pa.scalar(".000Z"), "")
        mapped = pa.table({
            "event_id": pc.binary_join_element_wise(pa.scalar("l"), rn, ""),
            "timestamp": timestamp,
            "account_id": account_k,
            "event_type": type_k,
            "source_host": host_k,
            "source_ip": pa.nulls(len(idx), pa.string()),
            "auth_method": pa.array(method_names[method_code[position]]),
            "mfa_used": pa.array(is_success & mfa_required[position]),
            "session_id": pc.if_else(pa.array(is_success),
                                     pc.binary_join_element_wise(pa.scalar("s"), rn, ""),
                                     pa.scalar(None, pa.string())),
        }).select(SCHEMA_COLUMNS)

        dates = pc.utf8_slice_codeunits(mapped["timestamp"], 0, 10)
        for date in pc.unique(dates).to_pylist():
            part = mapped.filter(pc.equal(dates, date))
            write_partition(part, events_root, date, f"part-lanl-{chunk_no:05d}", compression)
        chunk_no += 1
        rows_out += mapped.num_rows

    records = policy_mod.to_records(sorted(seen_accounts), policy_seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "policy.json").write_text(
        json.dumps({"$id": "sentinel-forge/lanl-synthetic-account-policy/v1", "records": records}) + "\n",
        encoding="utf-8")

    manifest = {
        "mapperVersion": GENERATOR_VERSION,
        "source": {"file": str(auth_path).replace("\\", "/"), "bytes": auth_path.stat().st_size},
        "options": {"anchor": anchor, "accountField": account_field, "orientations": list(orientations),
                    "excludeMachineAccounts": exclude_machine_accounts, "onUnknownStatus": on_unknown_status,
                    "policySeed": policy_seed, "maxRows": max_rows},
        "counts": {"rowsRead": rows_in, "rowsWritten": rows_out, "dropped": dict(dropped),
                   "accounts": len(seen_accounts), "chunks": chunk_no},
        "valueProfile": {c: dict(profile[c].most_common(50)) for c in PROFILED},
        "syntheticFields": ["timestamp", "auth_method", "mfa_used"],
        "note": "See docs/data-sources.md. valueProfile covers ALL rows read, before any filtering; "
                "use it to confirm the orientation/status/machine-account assumptions.",
    }
    (out_dir / "mapper_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def map_redteam_file(redteam_path: Path, out_path: Path, *, anchor: str = DEFAULT_ANCHOR) -> int:
    """Map ``redteam.txt`` (``time,user@domain,source computer,destination computer``) to JSONL."""
    anchor_epoch = _anchor_epoch(anchor)
    count = 0
    with out_path.open("w", encoding="utf-8", newline="\n") as out:
        reader = pacsv.open_csv(
            pa.input_stream(str(redteam_path), compression="detect"),
            read_options=pacsv.ReadOptions(column_names=["time", "user", "src_comp", "dst_comp"]),
            convert_options=pacsv.ConvertOptions(column_types={"time": pa.int64(), "user": pa.string(),
                                                               "src_comp": pa.string(), "dst_comp": pa.string()}))
        for batch in reader:
            for row in batch.to_pylist():
                stamp = dt.datetime.fromtimestamp(anchor_epoch + row["time"] - 1, dt.timezone.utc)
                out.write(json.dumps({
                    "timestamp": stamp.strftime("%Y-%m-%dT%H:%M:%S") + ".000Z",
                    "account_id": row["user"], "source_host": row["src_comp"],
                    "destination_host": row["dst_comp"], "lanlTime": row["time"],
                }, separators=(",", ":")) + "\n")
                count += 1
    return count


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auth", required=True, type=Path, help="auth.txt or auth.txt.gz")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--redteam", type=Path, help="redteam.txt[.gz] to map to redteam_events.jsonl")
    ap.add_argument("--anchor", default=DEFAULT_ANCHOR, help="ISO date that LANL time=1 maps to")
    ap.add_argument("--account-field", default="source", choices=["source", "destination"])
    ap.add_argument("--orientations", nargs="+", default=["LogOn"])
    ap.add_argument("--keep-machine-accounts", action="store_true")
    ap.add_argument("--on-unknown-status", default="error", choices=["error", "drop"])
    ap.add_argument("--policy-seed", type=int, default=42)
    ap.add_argument("--max-rows", type=int, help="stop after N raw rows (for trial runs)")
    ap.add_argument("--compression", default="snappy", choices=["snappy", "zstd", "gzip", "none"])
    args = ap.parse_args(argv)

    manifest = map_auth_file(args.auth, args.out, anchor=args.anchor, account_field=args.account_field,
                             orientations=tuple(args.orientations),
                             exclude_machine_accounts=not args.keep_machine_accounts,
                             on_unknown_status=args.on_unknown_status, policy_seed=args.policy_seed,
                             compression=args.compression, max_rows=args.max_rows)
    c = manifest["counts"]
    print(f"Read {c['rowsRead']:,} rows, wrote {c['rowsWritten']:,}; dropped {c['dropped']}; accounts {c['accounts']:,}")
    if args.redteam:
        n = map_redteam_file(args.redteam, args.out / "redteam_events.jsonl", anchor=args.anchor)
        print(f"Mapped {n:,} red-team events to {args.out / 'redteam_events.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
