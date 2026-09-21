"""Permanent regression tests for the harness bugs an independent external
review found (round 2 — see docs/spec/independent-review-corrections.md):
same-run filename collisions on misclassification, stale output from a
failed run being indistinguishable from a fresh one, and the "latest
successful run" pointer being updatable even on partial failure.

These use synthetic run_one results (injected via execute_run's
run_one_fn parameter) and a throwaway temp directory — no real LLM calls,
no writes anywhere near real data, so this runs fast and offline.

Usage:
    python compiler/test/test_evaluate_end_to_end.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_end_to_end as e2e  # noqa: E402


def _fake_row(report_name: str, behaviour_id: str) -> dict:
    return {
        "report": report_name,
        "extractedBehaviourId": behaviour_id,
        "stage3Status": "supported",
        "compiledSpec": {"behaviourId": behaviour_id, "recipe": "PolicyCompare"},
    }


def _tmp_dirs():
    # execute_run stores each compiledSpecPath relative to repo_root (so
    # EndToEndCheck.scala can resolve it the same way for real runs) — the
    # scratch directory has to live inside the repo for that to hold during
    # a test, not under the OS temp dir. Gitignored; removed after each case.
    scratch_root = REPO_ROOT / "compiler" / "test" / ".e2e_test_scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="run_", dir=str(scratch_root)))
    return tmp, tmp / "base", tmp / "base" / "LATEST_SUCCESSFUL_RUN.txt"


def case_two_reports_same_behaviour_do_not_collide():
    """Round 2 finding: filenames were based on the PREDICTED behaviourId,
    so two reports misclassified into the same behaviour would silently
    overwrite each other's compiled spec."""
    tmp, base_dir, pointer = _tmp_dirs()
    try:
        # Two DIFFERENT reports, both (mis)classified as the SAME behaviourId —
        # exactly the collision scenario the review described.
        def fake_run_one(report_name: str) -> dict:
            return _fake_row(report_name, "mfa-bypass-on-required-account")

        manifest = e2e.execute_run(["report-alpha", "report-beta"], REPO_ROOT, base_dir,
                                    pointer, run_one_fn=fake_run_one, run_id="run_test_collision")
        run_dir = base_dir / "run_test_collision"

        alpha_path = run_dir / "report-alpha.compiled.json"
        beta_path = run_dir / "report-beta.compiled.json"
        assert alpha_path.exists(), "report-alpha's own compiled spec must exist"
        assert beta_path.exists(), "report-beta's own compiled spec must exist"
        assert manifest["reports"]["report-alpha"]["compiledSpecPath"] != \
               manifest["reports"]["report-beta"]["compiledSpecPath"], \
               "two reports classified into the same behaviour must not share a file"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_failed_run_does_not_update_latest_pointer():
    """Round 2 finding: a failed report left the PREVIOUS run's file
    sitting there looking current, with no way to tell it was stale."""
    tmp, base_dir, pointer = _tmp_dirs()
    try:
        # First, a real successful run, to give the pointer something to
        # (wrongly) still point at if the fix regresses.
        def good_run_one(report_name: str) -> dict:
            return _fake_row(report_name, "repeated-failed-login-then-success")

        e2e.execute_run(["report-good"], REPO_ROOT, base_dir, pointer,
                         run_one_fn=good_run_one, run_id="run_good")
        assert pointer.read_text().strip() == "run_good"

        def failing_run_one(report_name: str) -> dict:
            raise e2e.RunFailedError(f"{report_name}: synthetic failure for this test")

        try:
            e2e.execute_run(["report-bad"], REPO_ROOT, base_dir, pointer,
                             run_one_fn=failing_run_one, run_id="run_bad")
            raise AssertionError("expected RunFailedError to propagate")
        except e2e.RunFailedError:
            pass

        assert pointer.read_text().strip() == "run_good", \
            "a failed run must never update the latest-successful-run pointer"

        failed_manifest = json.loads((base_dir / "run_bad" / "manifest.json").read_text())
        assert failed_manifest["status"] == "FAILED"
        assert "failureReason" in failed_manifest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_partial_success_before_failure_is_marked_failed_overall():
    """A run where reports 1-2 succeed and report 3 fails must be marked
    FAILED overall, not silently accepted with 2/3 results."""
    tmp, base_dir, pointer = _tmp_dirs()
    try:
        def mixed_run_one(report_name: str) -> dict:
            if report_name == "report-3":
                raise e2e.RunFailedError("report-3: synthetic failure")
            return _fake_row(report_name, "password-spray-across-accounts")

        try:
            e2e.execute_run(["report-1", "report-2", "report-3"], REPO_ROOT, base_dir,
                             pointer, run_one_fn=mixed_run_one, run_id="run_mixed")
            raise AssertionError("expected RunFailedError")
        except e2e.RunFailedError:
            pass

        assert not pointer.exists(), "pointer must not be created for a run that never fully succeeded"
        manifest = json.loads((base_dir / "run_mixed" / "manifest.json").read_text())
        assert manifest["status"] == "FAILED"
        assert "report-1" in manifest["reports"] and "report-2" in manifest["reports"], \
            "the partial manifest should still record what DID succeed, for inspection"
        assert "report-3" not in manifest["reports"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_two_separate_runs_do_not_collide_across_runs():
    """Every run gets its own timestamped directory — a second run must
    not overwrite or read the first run's files."""
    tmp, base_dir, pointer = _tmp_dirs()
    try:
        def run_one_a(report_name: str) -> dict:
            return _fake_row(report_name, "concurrent-sessions-different-hosts")

        e2e.execute_run(["report-x"], REPO_ROOT, base_dir, pointer, run_one_fn=run_one_a, run_id="run_A")
        e2e.execute_run(["report-x"], REPO_ROOT, base_dir, pointer, run_one_fn=run_one_a, run_id="run_B")

        assert (base_dir / "run_A" / "report-x.compiled.json").exists()
        assert (base_dir / "run_B" / "report-x.compiled.json").exists()
        assert pointer.read_text().strip() == "run_B", "pointer should reflect the most recent successful run"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_compiled_spec_file_is_single_line_json():
    """A real bug found while proving this harness end to end: writing the
    spec pretty-printed (multi-line) silently broke CompiledSpec.scala's
    single-line JSON reader."""
    tmp, base_dir, pointer = _tmp_dirs()
    try:
        def fake_run_one(report_name: str) -> dict:
            return _fake_row(report_name, "repeated-failed-login-then-success")

        e2e.execute_run(["report-y"], REPO_ROOT, base_dir, pointer, run_one_fn=fake_run_one, run_id="run_fmt")
        content = (base_dir / "run_fmt" / "report-y.compiled.json").read_text()
        assert "\n" not in content.strip(), "compiled spec file must be single-line JSON, not pretty-printed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_")]


def main() -> None:
    failures = []
    for case in CASES:
        try:
            case()
            print(f"OK   {case.__name__}")
        except AssertionError as e:
            failures.append(case.__name__)
            print(f"FAIL {case.__name__}: {e}")

    if failures:
        raise SystemExit(f"\n{len(failures)}/{len(CASES)} case(s) failed: {failures}")
    print(f"\nAll {len(CASES)} cases passed.")


if __name__ == "__main__":
    main()
