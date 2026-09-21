"""Tests for the scaled-dataset generator (scripts/datagen).

Run directly (``python tests/datagen/test_generator.py``) or collect with pytest.
No network, no Spark.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import random
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pyarrow.parquet as pq  # noqa: E402

from scripts.datagen import generate  # noqa: E402
from scripts.datagen.background import SCHEMA_COLUMNS, Background, BackgroundConfig  # noqa: E402
from scripts.datagen.incidents import BUILDERS, Context, inject  # noqa: E402
from scripts.datagen.params import BEHAVIOUR_IDS, DetectionParams  # noqa: E402
from scripts.datagen.refdetect import detect, status_for  # noqa: E402

PARAMS = DetectionParams.from_compiled_specs()
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _args(out, **overrides):
    defaults = dict(out=str(out), seed=42, start_date="2026-01-05", days=3, accounts=1500,
                    incidents_per_behaviour=40, background_dir=None, background_policy=None,
                    compression="snappy", overwrite=False, verify=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _load(out: Path):
    events = pq.ParquetDataset(out / "events").read(columns=SCHEMA_COLUMNS).to_pylist()
    policy = json.loads((out / "policy.json").read_text(encoding="utf-8"))["records"]
    labels = [json.loads(line) for line in (out / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
    return events, policy, labels


def _small_context(seed=1):
    start = int(dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc).timestamp())
    return Context(rng=random.Random(seed), params=PARAMS, start_sec=start, end_sec=start + 3 * 86400)


def test_params_come_from_compiled_specs():
    assert (PARAMS.b1_window_s, PARAMS.b1_fail_threshold) == (120, 5)
    assert (PARAMS.b2_window_s, PARAMS.b2_distinct_threshold) == (600, 4)
    assert (PARAMS.b3_window_s, PARAMS.b3_host_threshold) == (900, 2)


def test_every_incident_kind_is_generated():
    ctx = _small_context()
    inject(ctx, max(len(b) for b in BUILDERS.values()))   # one full cycle of every builder
    kinds = {}
    for label in ctx.labels:
        kinds.setdefault(label["behaviourId"], set()).add(label["kind"])
    windowed = {"positive", "boundary-positive", "boundary-negative", "hard-negative"}
    policy_based = {"positive", "hard-negative", "insufficient-context"}
    for behaviour in BEHAVIOUR_IDS[:3]:
        assert kinds[behaviour] == windowed, (behaviour, kinds[behaviour])
    for behaviour in BEHAVIOUR_IDS[3:]:
        assert kinds[behaviour] == policy_based, (behaviour, kinds[behaviour])
    assert {l["expectedStatus"] for l in ctx.labels} == {"alert", "no_alert", "insufficient_context"}


def test_labels_agree_with_independent_reference_detector():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ds"
        manifest = generate.build(_args(out, verify=True))
        v = manifest["verification"]
        assert v["passed"], v
        assert v["labelMismatches"] == 0 and v["unexpectedAlerts"] == 0 and v["missingAlerts"] == 0


def test_background_alone_raises_no_alerts_across_seeds():
    for seed in (1, 2, 3):
        bg = Background(BackgroundConfig(n_accounts=3000, days=2, seed=seed))
        events, counter = [], 0
        for day in range(2):
            table, counter = bg.generate_day(day, counter)
            events += table.to_pylist()
        assert len(events) > 10_000
        alerts = detect(events, list(bg.iter_policy_records()), PARAMS)
        assert alerts == [], f"seed {seed}: background triggered {len(alerts)} alert(s): {alerts[:3]}"


def test_reference_detector_rejects_a_label_that_is_off_by_one_second():
    """Mutation check: the cross-check must be able to fail, not just pass.

    Each boundary incident is nudged one second across its boundary; the detector must
    then disagree with the (now stale) label.
    """
    from scripts.datagen.incidents import b1_pos_boundary, b1_neg_boundary, b2_pos_boundary, b3_pos_boundary
    ctx = _small_context(seed=5)
    for builder in (b1_pos_boundary, b1_neg_boundary, b2_pos_boundary, b3_pos_boundary):
        builder(ctx)
    assert len(ctx.labels) == 4
    baseline = detect(ctx.events, ctx.policy, PARAMS)
    assert all(status_for(l, baseline)[0] == l["expectedStatus"] for l in ctx.labels), "unmutated labels must agree"

    for label in ctx.labels:
        mutated = copy.deepcopy(ctx.events)
        by_id = {e["event_id"]: e for e in mutated}
        earliest = min((by_id[i] for i in label["eventIds"]), key=lambda e: e["timestamp"])
        # expected alert -> push the earliest event 1s out of the window; expected no_alert -> pull it 1s in
        shift = -1 if label["expectedStatus"] == "alert" else 1
        moved = dt.datetime.strptime(earliest["timestamp"][:19], "%Y-%m-%dT%H:%M:%S") + dt.timedelta(seconds=shift)
        earliest["timestamp"] = moved.strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"
        status, _ = status_for(label, detect(mutated, ctx.policy, PARAMS))
        assert status != label["expectedStatus"], (
            f"1s shift of a {label['kind']} {label['behaviourId']} incident did not change the detector's answer")


def test_generation_is_deterministic_and_seed_sensitive():
    with tempfile.TemporaryDirectory() as tmp:
        a, b, c = (Path(tmp) / n for n in "abc")
        ma = generate.build(_args(a))
        mb = generate.build(_args(b))
        mc = generate.build(_args(c, seed=43))
        assert ma["sha256"] == mb["sha256"]
        ea, eb = pq.ParquetDataset(a / "events").read(), pq.ParquetDataset(b / "events").read()
        assert ea.equals(eb)
        assert ma["sha256"]["labels.jsonl"] != mc["sha256"]["labels.jsonl"]


def test_schema_and_value_constraints():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ds"
        generate.build(_args(out))
        events, policy, _ = _load(out)
        assert len({e["event_id"] for e in events}) == len(events), "event_id must be unique"
        assert all(ISO_RE.match(e["timestamp"]) for e in events)
        assert {e["event_type"] for e in events} == {"login_success", "login_failure"}
        assert {e["auth_method"] for e in events} <= {"password", "token", "certificate"}
        assert all(isinstance(e["mfa_used"], bool) for e in events)
        assert all((e["session_id"] is not None) == (e["event_type"] == "login_success") for e in events)
        assert all(e["account_id"] and e["source_host"] for e in events)
        assert len({p["account_id"] for p in policy}) == len(policy), "policy rows unique per account"
        # event_date partition directories must agree with the event timestamps
        for path in (out / "events").glob("event_date=*"):
            date = path.name.split("=")[1]
            rows = pq.ParquetDataset(path).read(columns=["timestamp"]).to_pylist()
            assert rows and all(r["timestamp"].startswith(date) for r in rows), path


def test_incidents_use_isolated_entities_and_whole_seconds():
    ctx = _small_context(seed=9)
    inject(ctx, 40)
    events = {e["event_id"]: e for e in ctx.events}
    account_owner, host_owner = {}, {}
    labelled = set()
    for label in ctx.labels:
        group = frozenset(label["eventIds"])     # an incident's identity = its event set
        for event_id in label["eventIds"]:
            e = events[event_id]
            for owners, value in ((account_owner, e["account_id"]), (host_owner, e["source_host"])):
                assert owners.setdefault(value, group) == group, f"{value} is shared between incidents"
            assert e["timestamp"].endswith(".000Z"), "incident events sit on whole seconds"
            labelled.add(event_id)
    assert labelled == set(events), "every injected event belongs to a labelled incident"


def test_refuses_to_overwrite_existing_output():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ds"
        generate.build(_args(out))
        try:
            generate.build(_args(out))
        except SystemExit as exc:
            assert "overwrite" in str(exc).lower()
        else:
            raise AssertionError("second build over existing output should have been refused")
        generate.build(_args(out, overwrite=True))  # explicit overwrite is allowed


def _run_all():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK   {name}")
            except Exception as exc:  # noqa: BLE001 - report every failure, then exit non-zero
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    total = sum(1 for n in globals() if n.startswith("test_"))
    print(f"\n{total - failures}/{total} cases passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
