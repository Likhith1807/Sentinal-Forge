"""Assemble a labelled authentication dataset: background events + injected incidents.

    python -m scripts.datagen.generate --out data/generated/dev --accounts 20000 --days 7

Output layout under ``--out``:
    events/event_date=YYYY-MM-DD/part-background-*.parquet   synthetic background (default)
    events/event_date=YYYY-MM-DD/part-incidents.parquet      injected incident events
    labels.jsonl        one ground-truth label per incident (independent of any detector)
    policy.json         synthetic account-policy table (background + incident accounts)
    manifest.json       seed, parameters, counts, file hashes, verification result

With ``--background-dir`` (e.g. the LANL mapper's ``events`` directory) the background is
left untouched and incident files are added into its partitions; ``--out`` then holds only
labels, policy and the manifest. See docs/data-sources.md for what is real vs synthetic.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import random
import shutil
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import GENERATOR_VERSION
from .background import SCHEMA_COLUMNS, Background, BackgroundConfig, write_partition
from .incidents import Context, inject
from .params import DetectionParams
from .refdetect import detect, status_for

VERIFY_ROW_LIMIT = 3_000_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_policy(path: Path, *record_iterables) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write('{"$id": "sentinel-forge/generated-account-policy/v1", "records": [\n')
        first = True
        for records in record_iterables:
            for record in records:
                fh.write(("" if first else ",\n") + json.dumps(record, separators=(",", ":")))
                first = False
                count += 1
        fh.write("\n]}\n")
    return count


def _partition_dates(events_root: Path) -> list[str]:
    return sorted(p.name.split("=", 1)[1] for p in events_root.glob("event_date=*") if p.is_dir())


def _incident_table(events: list[dict]) -> pa.Table:
    types = {"mfa_used": pa.bool_()}
    return pa.table({c: pa.array([e[c] for e in events], types.get(c, pa.string())) for c in SCHEMA_COLUMNS})


def build(args: argparse.Namespace) -> dict:
    started = time.time()
    params = DetectionParams.from_compiled_specs()
    out = Path(args.out)
    external = args.background_dir is not None
    events_root = Path(args.background_dir) if external else out / "events"

    managed = [out / "labels.jsonl", out / "policy.json", out / "manifest.json"]
    if not external:
        managed.append(events_root)
    existing = [p for p in managed if p.exists()]
    if existing and not args.overwrite:
        raise SystemExit(f"Refusing to overwrite existing output: {[str(p) for p in existing]} (use --overwrite).")
    if args.overwrite and not external and events_root.exists():
        shutil.rmtree(events_root)
    out.mkdir(parents=True, exist_ok=True)

    # ---- background -------------------------------------------------------------
    if external:
        if not args.background_policy:
            raise SystemExit("--background-dir requires --background-policy (the policy the background is compliant with).")
        dates = _partition_dates(events_root)
        if not dates:
            raise SystemExit(f"No event_date=* partitions under {events_root}")
        policy_doc = json.loads(Path(args.background_policy).read_text(encoding="utf-8"))
        background_policy_iter = iter(policy_doc["records"])
        first_day, last_day = dt.date.fromisoformat(dates[0]), dt.date.fromisoformat(dates[-1])
        background_events = None  # not counted: potentially billions of rows
        background_kind = "external"
    else:
        cfg = BackgroundConfig(n_accounts=args.accounts, start=dt.date.fromisoformat(args.start_date),
                               days=args.days, seed=args.seed)
        background = Background(cfg)
        counter = 0
        for day in range(cfg.days):
            table, counter = background.generate_day(day, counter)
            write_partition(table, events_root, background.day_date(day).isoformat(),
                            "part-background-0000", args.compression)
            print(f"  background day {day + 1}/{cfg.days}: {table.num_rows:,} events", flush=True)
        background_events = counter
        background_policy_iter = background.iter_policy_records()
        first_day, last_day = cfg.start, cfg.start + dt.timedelta(days=cfg.days - 1)
        background_kind = "synthetic"

    # ---- incidents --------------------------------------------------------------
    start_sec = int(dt.datetime.combine(first_day, dt.time(), dt.timezone.utc).timestamp())
    end_sec = int(dt.datetime.combine(last_day + dt.timedelta(days=1), dt.time(), dt.timezone.utc).timestamp())
    ctx = Context(rng=random.Random(args.seed), params=params, start_sec=start_sec, end_sec=end_sec)
    inject(ctx, args.incidents_per_behaviour)

    by_date = collections.defaultdict(list)
    for event in ctx.events:
        by_date[event["timestamp"][:10]].append(event)
    for date, rows in sorted(by_date.items()):
        target = events_root / f"event_date={date}" / "part-incidents.parquet"
        if external and target.exists() and not args.overwrite:
            raise SystemExit(f"{target} already exists (use --overwrite).")
        rows.sort(key=lambda e: (e["timestamp"], e["event_id"]))
        write_partition(_incident_table(rows), events_root, date, "part-incidents", args.compression)

    # ---- labels / policy / manifest --------------------------------------------------
    with (out / "labels.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for label in ctx.labels:
            fh.write(json.dumps(label, separators=(",", ":")) + "\n")
    policy_count = _write_policy(out / "policy.json", background_policy_iter, ctx.policy)

    kinds = collections.Counter((l["behaviourId"], l["kind"], l["expectedStatus"]) for l in ctx.labels)
    manifest = {
        "generatorVersion": GENERATOR_VERSION,
        "seed": args.seed,
        "backgroundKind": background_kind,
        "dateRange": [first_day.isoformat(), last_day.isoformat()],
        "config": {"accounts": None if external else args.accounts,
                   "days": (last_day - first_day).days + 1,
                   "incidentsPerBehaviour": args.incidents_per_behaviour,
                   "compression": args.compression},
        "detectionParams": params.__dict__,
        "counts": {"backgroundEvents": background_events, "incidentEvents": len(ctx.events),
                   "incidents": len(ctx.labels), "policyRecords": policy_count},
        "incidentKinds": [{"behaviourId": b, "kind": k, "expectedStatus": s, "count": n}
                          for (b, k, s), n in sorted(kinds.items())],
        "eventsRoot": str(events_root).replace("\\", "/"),
        "sha256": {"labels.jsonl": _sha256(out / "labels.jsonl"), "policy.json": _sha256(out / "policy.json")},
        "verification": None,
        "elapsedSeconds": round(time.time() - started, 1),
    }
    if args.verify:
        manifest["verification"] = verify(events_root, out / "policy.json", ctx.labels, params)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(events_root: Path, policy_path: Path, labels: list[dict], params: DetectionParams) -> dict:
    """Cross-check every label, and the background, with the independent reference detector."""
    table = pq.ParquetDataset(events_root).read(columns=SCHEMA_COLUMNS)
    if table.num_rows > VERIFY_ROW_LIMIT:
        return {"skipped": f"{table.num_rows:,} rows exceeds the {VERIFY_ROW_LIMIT:,}-row limit "
                           "of the pure-Python reference detector"}
    events = table.to_pylist()
    policy = json.loads(policy_path.read_text(encoding="utf-8"))["records"]
    alerts = detect(events, policy, params)

    mismatches = []
    for label in labels:
        status, trigger = status_for(label, alerts)
        wrong_trigger = label["expectedTriggerEventId"] and trigger != label["expectedTriggerEventId"]
        if status != label["expectedStatus"] or wrong_trigger:
            mismatches.append({"incidentId": label["incidentId"], "expected": label["expectedStatus"], "actual": status})
    expected = {(l["behaviourId"], l["expectedTriggerEventId"]) for l in labels if l["expectedStatus"] != "no_alert"}
    actual = {(a["behaviourId"], a["eventId"]) for a in alerts}
    return {
        "rowsChecked": table.num_rows,
        "labelMismatches": len(mismatches),
        "unexpectedAlerts": len(actual - expected),
        "missingAlerts": len(expected - actual),
        "passed": not mismatches and actual == expected,
        "firstMismatches": mismatches[:5],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start-date", default="2026-01-05")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--accounts", type=int, default=20_000, help="synthetic background accounts")
    ap.add_argument("--incidents-per-behaviour", type=int, default=400)
    ap.add_argument("--background-dir", help="existing partitioned events directory to inject into (e.g. LANL mapper output)")
    ap.add_argument("--background-policy", help="policy.json for --background-dir")
    ap.add_argument("--compression", default="snappy", choices=["snappy", "zstd", "gzip", "none"])
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--verify", action="store_true", help="cross-check labels with the reference detector")
    manifest = build(ap.parse_args(argv))
    c = manifest["counts"]
    background = f"{c['backgroundEvents']:,}" if c["backgroundEvents"] is not None else "external"
    print(f"Wrote {c['incidents']:,} incidents ({c['incidentEvents']:,} events); background events: "
          f"{background}; policy records: {c['policyRecords']:,}; {manifest['elapsedSeconds']}s")
    if manifest["verification"]:
        print("Verification:", json.dumps(manifest["verification"]))
        return 0 if manifest["verification"].get("passed", True) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
