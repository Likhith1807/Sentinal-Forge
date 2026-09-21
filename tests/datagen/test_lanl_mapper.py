"""Tests for the LANL cyber1 mapper (scripts/datagen/lanl_mapper.py), on a small fake LANL file.

The real LANL data is not available yet, so these tests pin the mapper's *behaviour* against
the published field description. Run directly or collect with pytest. No network, no Spark.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pyarrow.parquet as pq  # noqa: E402

from scripts.datagen import generate  # noqa: E402
from scripts.datagen.lanl_mapper import UnknownValueError, map_auth_file, map_redteam_file  # noqa: E402

FAKE_AUTH = "\n".join([
    "1,U1@DOM1,U1@DOM1,C1,C2,Kerberos,Network,LogOn,Success",
    "2,U1@DOM1,U1@DOM1,C1,C2,Kerberos,Network,LogOff,Success",      # not a logon -> dropped
    "3,C1$@DOM1,C1$@DOM1,C1,C1,Negotiate,Service,LogOn,Success",     # machine account -> dropped
    "4,U2@DOM1,U2@DOM1,C3,C4,NTLM,Network,LogOn,Fail",
    "5,?,?,?,?,?,?,LogOn,Success",                                   # missing identity -> dropped
    "6,U3@DOM1,U4@DOM1,C5,C6,NTLM,Interactive,LogOn,Success",       # src != dst user
    "86401,U1@DOM1,U1@DOM1,C1,C5,NTLM,Interactive,LogOn,Success",   # next day
    "86500,U2@DOM1,U2@DOM1,C3,C4,Kerberos,Network,LogOn,Success",
]) + "\n"


def _write(tmp: Path, text: str = FAKE_AUTH, gz: bool = False) -> Path:
    path = tmp / ("auth.txt.gz" if gz else "auth.txt")
    if gz:
        with gzip.open(path, "wt", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _events(out: Path) -> list[dict]:
    rows = pq.ParquetDataset(out / "events").read().to_pylist()
    return sorted(rows, key=lambda r: r["event_id"])


def test_maps_fields_dates_and_drops_the_right_rows():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        manifest = map_auth_file(_write(tmp), tmp / "out", anchor="2026-01-05")
        rows = _events(tmp / "out")
        assert manifest["counts"]["rowsRead"] == 8 and manifest["counts"]["rowsWritten"] == 5
        assert manifest["counts"]["dropped"] == {"orientation_not_selected": 1, "missing_account_or_host": 1,
                                                 "machine_account": 1}
        assert [r["event_id"] for r in rows] == ["l000000000000", "l000000000003", "l000000000005",
                                                 "l000000000006", "l000000000007"]   # 0-based source row
        first = rows[0]
        assert first["timestamp"] == "2026-01-05T00:00:00.000Z"              # time=1 -> anchor midnight
        assert first["account_id"] == "U1@DOM1" and first["source_host"] == "C1"
        assert first["event_type"] == "login_success" and first["source_ip"] is None
        assert rows[1]["event_type"] == "login_failure" and rows[1]["mfa_used"] is False
        assert rows[3]["timestamp"] == "2026-01-06T00:00:00.000Z"            # time=86401 -> next day
        assert {r["event_date"] for r in rows} == {"2026-01-05", "2026-01-06"}
        # source user is the account by default; destination is available as an option
        assert rows[2]["account_id"] == "U3@DOM1"


def test_destination_account_option():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        map_auth_file(_write(tmp), tmp / "out", account_field="destination")
        assert "U4@DOM1" in {r["account_id"] for r in _events(tmp / "out")}


def test_gzip_and_plain_input_give_identical_output_and_chunking_does_not_change_it():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        map_auth_file(_write(tmp), tmp / "plain")
        map_auth_file(_write(tmp, gz=True), tmp / "gz")
        map_auth_file(_write(tmp), tmp / "tiny_blocks", block_size=64)   # forces many chunks
        base = _events(tmp / "plain")
        assert base == _events(tmp / "gz")
        assert base == _events(tmp / "tiny_blocks")
        assert json.loads((tmp / "tiny_blocks" / "mapper_manifest.json").read_text())["counts"]["chunks"] > 1


def test_synthetic_fields_follow_the_policy_and_are_compliant():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        map_auth_file(_write(tmp), tmp / "out")
        policy = {r["account_id"]: r for r in
                  json.loads((tmp / "out" / "policy.json").read_text(encoding="utf-8"))["records"]}
        for row in _events(tmp / "out"):
            rec = policy[row["account_id"]]
            assert row["auth_method"] == rec["expected_auth_method"]
            expected_mfa = rec["mfa_required"] and row["event_type"] == "login_success"
            assert row["mfa_used"] is expected_mfa


def test_value_profile_covers_every_row_before_filtering():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        manifest = map_auth_file(_write(tmp), tmp / "out")
        profile = manifest["valueProfile"]
        assert profile["orientation"] == {"LogOn": 7, "LogOff": 1}
        assert profile["status"] == {"Success": 7, "Fail": 1}
        assert profile["auth_type"]["Kerberos"] == 3
        assert manifest["syntheticFields"] == ["timestamp", "auth_method", "mfa_used"]


def test_unknown_status_is_an_error_unless_explicitly_dropped():
    bad = FAKE_AUTH + "9,U5@DOM1,U5@DOM1,C7,C8,NTLM,Network,LogOn,Weird\n"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = _write(tmp, bad)
        try:
            map_auth_file(path, tmp / "strict")
        except UnknownValueError as exc:
            assert "Weird" in str(exc)
        else:
            raise AssertionError("an unrecognised status value must not be silently accepted")
        manifest = map_auth_file(path, tmp / "lenient", on_unknown_status="drop")
        assert manifest["counts"]["dropped"]["unknown_status"] == 1
        assert manifest["counts"]["rowsWritten"] == 5


def test_non_integer_time_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        try:
            map_auth_file(_write(tmp, "x,U1@DOM1,U1@DOM1,C1,C2,NTLM,Network,LogOn,Success\n"), tmp / "out")
        except UnknownValueError:
            return
        raise AssertionError("non-integer time must raise")


def test_redteam_mapping():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        red = tmp / "redteam.txt"
        red.write_text("150885,U748@DOM1,C17693,C1003\n1,U1@DOM1,C1,C2\n", encoding="utf-8", newline="\n")
        n = map_redteam_file(red, tmp / "red.jsonl", anchor="2026-01-05")
        rows = [json.loads(l) for l in (tmp / "red.jsonl").read_text().splitlines()]
        assert n == 2 and rows[1]["timestamp"] == "2026-01-05T00:00:00.000Z"
        assert rows[0] == {"timestamp": "2026-01-06T17:54:44.000Z", "account_id": "U748@DOM1",
                           "source_host": "C17693", "destination_host": "C1003", "lanlTime": 150885}


def test_mapped_output_works_as_background_for_incident_injection():
    """The full integration path: LANL-mapped background + injected incidents, cross-checked."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lanl = tmp / "lanl"
        map_auth_file(_write(tmp), lanl)
        args = argparse.Namespace(
            out=str(tmp / "out"), seed=7, start_date="2026-01-05", days=1, accounts=0,
            incidents_per_behaviour=20, background_dir=str(lanl / "events"),
            background_policy=str(lanl / "policy.json"), compression="snappy", overwrite=False, verify=True)
        manifest = generate.build(args)
        assert manifest["backgroundKind"] == "external"
        assert manifest["verification"]["passed"], manifest["verification"]
        # the background files were left untouched and incident files were added beside them
        names = {p.name for p in (lanl / "events").rglob("*.parquet")}
        assert "part-incidents.parquet" in names and any(n.startswith("part-lanl-") for n in names)
        labelled_policy = json.loads((tmp / "out" / "policy.json").read_text())["records"]
        assert any(r["account_id"] == "U1@DOM1" for r in labelled_policy)          # background account kept
        assert any(r["account_id"].startswith("ixu") for r in labelled_policy)     # plus incident accounts


def _run_all():
    failures = 0
    names = sorted(n for n in globals() if n.startswith("test_") and callable(globals()[n]))
    for name in names:
        try:
            globals()[name]()
            print(f"OK   {name}")
        except Exception as exc:  # noqa: BLE001 - report every failure, then exit non-zero
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(names) - failures}/{len(names)} cases passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
