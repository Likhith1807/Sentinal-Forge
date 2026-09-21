"""The test an independent review correctly pointed out was missing: does
report -> extract -> Stage 3 validate -> compile actually work as one
connected path, using compiler/src/spec_bridge.py, rather than every
Phase 4/5 result running against hand-authored
data/samples/ir/compiled/*.json files?

CORRECTION, round 2 (independent external review, 2026-09-16): the first
version of this script wrote to a SHARED directory using a filename based
on the PREDICTED behaviourId — so two reports classified into the same
behaviour within one run would silently overwrite each other, a failed
report left the previous run's file sitting there looking current, and
the Scala side had no way to tell a stale file from a fresh one. Fixed:
- every run gets its own timestamped directory — no cross-run collisions
- every compiled spec is filed under its REPORT name, never the predicted
  behaviourId — no same-run collision even if two reports get
  misclassified into the same behaviour
- a manifest.json in that directory is the one source of truth mapping
  report -> behaviourId -> compiled spec path, so nothing downstream has
  to assume a naming convention
- the run hard-stops (raises, non-zero exit) the moment any required
  report fails to extract, validate, or bridge — no silent partial output
- the "latest successful run" pointer is only written when ALL required
  reports succeeded, so a consumer that blindly follows it can never pick
  up a partial or failed run

Usage:
    python compiler/test/evaluate_end_to_end.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))
sys.path.insert(0, str(REPO_ROOT / "compiler" / "src"))

import transformer_extractor  # noqa: E402
import observability_checker as stage3  # noqa: E402
import spec_bridge  # noqa: E402

HELD_OUT_REPORTS = [
    "login-brute-force-003",
    "password-spray-003",
    "concurrent-sessions-003",
    "service-account-auth-003",
    "mfa-bypass-003",
]

BASE_DIR = REPO_ROOT / "data" / "samples" / "ir" / "compiled_from_extraction"
LATEST_POINTER = BASE_DIR / "LATEST_SUCCESSFUL_RUN.txt"


class RunFailedError(Exception):
    """Raised the moment any required report fails — the caller (main())
    lets this propagate to a non-zero exit rather than catching it and
    limping on with partial output."""


def extract_with_one_retry(report_name: str, text: str) -> dict:
    try:
        return transformer_extractor.extract(text)
    except Exception as e:  # noqa: BLE001 — real, pre-existing LLM JSON flakiness; see nlp/README.md
        print(f"  {report_name}: extraction failed once ({e}), retrying...")
        return transformer_extractor.extract(text)


def run_one(report_name: str) -> dict:
    report_path = REPO_ROOT / f"data/samples/reports/{report_name}.md"
    text = report_path.read_text(encoding="utf-8")

    extraction_spec = extract_with_one_retry(report_name, text)
    validation = stage3.validate(extraction_spec)

    if validation.status != "supported":
        raise RunFailedError(
            f"{report_name}: Stage 3 rejected the real extraction (behaviourId="
            f"{extraction_spec.get('behaviourId')!r}): {validation.notes}"
        )

    try:
        compiled = spec_bridge.build_compiled_spec(extraction_spec)
    except spec_bridge.UnbuildableSpecError as e:
        raise RunFailedError(f"{report_name}: bridge could not build a compiled spec: {e}") from e

    return {
        "report": report_name,
        "extractedBehaviourId": extraction_spec["behaviourId"],
        "stage3Status": validation.status,
        "compiledSpec": compiled,
    }


def execute_run(report_names: list[str], repo_root: Path, base_dir: Path, latest_pointer: Path,
                 run_one_fn=run_one, run_id: str | None = None) -> dict:
    """The actual run-isolation/manifest/hard-stop logic, pulled out of
    main() so compiler/test/test_evaluate_end_to_end.py can exercise it
    directly with synthetic run_one_fn results and a throwaway directory —
    no real LLM calls, no writes anywhere near real data. main() is the
    thin CLI wrapper around this.

    @param run_one_fn: injectable so tests can supply deterministic,
        network-free results (including a deliberate failure, to test the
        hard-stop path) instead of calling the real extractor.
    @raises RunFailedError: propagated from run_one_fn; caller decides
        what to do (main() exits non-zero).
    """
    run_id = run_id or datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    run_dir = base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"runId": run_id, "generatedAt": datetime.now(timezone.utc).isoformat(),
                       "reportsRequested": list(report_names), "reports": {}}

    try:
        for report_name in report_names:
            print(f"Processing {report_name}...")
            row = run_one_fn(report_name)

            # Filed by REPORT name, never by predicted behaviourId — two
            # reports landing on the same behaviourId in one run cannot
            # collide, because they're never sharing a filename.
            spec_path = run_dir / f"{report_name}.compiled.json"
            # Single-line, not pretty-printed: CompiledSpec.scala's loader
            # reads this via spark.read.json in default line-delimited
            # mode — multi-line JSON here would silently break that reader.
            spec_path.write_text(json.dumps(row["compiledSpec"]), encoding="utf-8")

            manifest["reports"][report_name] = {
                "behaviourId": row["extractedBehaviourId"],
                "stage3Status": row["stage3Status"],
                "compiledSpecPath": str(spec_path.relative_to(repo_root)),
            }
            print(f"  OK — behaviourId={row['extractedBehaviourId']!r}, wrote {spec_path.name}")

    except RunFailedError as e:
        manifest["status"] = "FAILED"
        manifest["failureReason"] = str(e)
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"\nRUN FAILED: {e}")
        print(f"Partial manifest written to {run_dir / 'manifest.json'} for inspection, "
              f"but {latest_pointer.name} was NOT updated — no consumer will pick this run up.")
        raise

    manifest["status"] = "SUCCESS"
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    latest_pointer.write_text(run_id, encoding="utf-8")

    print(f"\nAll {len(report_names)} requested reports succeeded.")
    print(f"Run directory: {run_dir}")
    print(f"{latest_pointer.name} now points to {run_id}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reports", nargs="+", default=HELD_OUT_REPORTS,
        help="Report names to include (default: all 5 held-out reports). Exists for targeted "
             "re-runs/debugging — the default (all 5, hard-stop on any failure) is what a real "
             "run should use; narrowing this hides a report from the hard-stop check, so any run "
             "using it must say so, not pass silently as 'the' end-to-end result.",
    )
    args = parser.parse_args()
    if list(args.reports) != HELD_OUT_REPORTS:
        print(f"NOTE: running a NARROWED report set {args.reports} — this is not the full "
              f"end-to-end check ({len(HELD_OUT_REPORTS)} reports); state that plainly wherever "
              f"this run's results are cited.\n")

    try:
        execute_run(list(args.reports), REPO_ROOT, BASE_DIR, LATEST_POINTER)
    except RunFailedError:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
