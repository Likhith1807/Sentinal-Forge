"""Consolidate the per-behaviour sample event logs under data/samples/replay/
into one partitioned Parquet event store under data/processed/events/,
matching what Stage 1 (Ingest & Normalise) is documented to produce from
authorised lab telemetry — see docs/examples/phase0-canonical-example.md and
docs/behaviours.md.

This is a one-off consolidation script for the Phase 0/1 sample data, not
the real Spark ingestion job (that's Phase 1's remaining step 8b / Phase 4's
execution engine) — it exists so the replay harness in Phase 5 has one real,
partitioned event store to query instead of five separate JSONL files, and
so every later phase can point at a concrete example of "normalized events
(Parquet)" rather than an abstract description.

Usage:
    python scripts/build_processed_dataset.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_DIR = REPO_ROOT / "data" / "samples" / "replay"
OUTPUT_DIR = REPO_ROOT / "data" / "processed" / "events"

# Every authentication_log_schema.json v1 field, in schema order, plus one
# provenance column (source_scenario) so a row can be traced back to the
# behaviour replay set it came from without inventing a new log field.
SCHEMA_FIELDS = [
    "event_id", "timestamp", "account_id", "event_type",
    "source_host", "source_ip", "auth_method", "mfa_used", "session_id",
]


def load_events() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(REPLAY_DIR.glob("*_events.jsonl")):
        scenario = path.stem.removesuffix("_events")
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                row = {field: event.get(field) for field in SCHEMA_FIELDS}
                row["source_scenario"] = scenario
                row["event_date"] = event["timestamp"][:10]
                rows.append(row)
    return rows


def main() -> None:
    rows = load_events()
    if not rows:
        raise SystemExit(f"No *_events.jsonl files found under {REPLAY_DIR}")

    table = pa.Table.from_pylist(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pq.write_to_dataset(table, root_path=str(OUTPUT_DIR), partition_cols=["event_date"])

    print(f"Wrote {len(rows)} events from {len(list(REPLAY_DIR.glob('*_events.jsonl')))} "
          f"scenario files to {OUTPUT_DIR}, partitioned by event_date.")
    dates = sorted({r["event_date"] for r in rows})
    print(f"Partitions: {dates}")


if __name__ == "__main__":
    main()
