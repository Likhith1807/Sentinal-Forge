"""Verify the fine-tuned extractor is deterministic across fresh processes — the actual, scoped
fix for the reliability gap `docs/spec/independent-review-corrections.md` and `nlp/README.md`
documented for the *prompted* extractor (Groq did not reproduce identical output at temperature=0).

Runs `--runs` (default 3) completely separate `python` subprocesses, each loading the checkpoint
from disk fresh and extracting every report in the given split, then compares every run's output
byte-for-byte. This is the strong version of the test: no shared process, no cached model, no
warm state that could hide a real source of nondeterminism (e.g. an uninitialized buffer, a
nondeterministic CUDA kernel, a fixed-seed RNG only seeded once at import time).

    python nlp/src/check_determinism.py --model-dir nlp/models/roberta-base --split test \
        --out experiments/results/phaseC_determinism_check.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))


def _single_run(model_dir: str, split: str) -> list[dict]:
    import finetuned_extractor
    from corpus_dataset import CORPUS_DIR
    splits = json.loads((CORPUS_DIR / "splits.json").read_text(encoding="utf-8"))["reports"]
    report_ids = sorted(rid for rid, s in splits.items() if s == split)
    out = []
    for report_id in report_ids:
        text = (CORPUS_DIR / "reports" / f"{report_id}.md").read_text(encoding="utf-8")
        r = finetuned_extractor.extract(text, model_dir)
        out.append({"reportId": report_id, "behaviourId": r.behaviourId, "requiredFields": sorted(r.requiredFields),
                    "policyFields": sorted(r.policyFields), "threshold": r.threshold, "timeWindow": r.timeWindow,
                    "provenance": r.provenance})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--_single-run", action="store_true", help=argparse.SUPPRESS)  # internal, used by the subprocess
    args = ap.parse_args(argv)

    if getattr(args, "_single_run"):
        print(json.dumps(_single_run(args.model_dir, args.split)))
        return 0

    outputs = []
    for i in range(args.runs):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__)), "--model-dir", args.model_dir, "--split", args.split, "--_single-run"],
            capture_output=True, text=True, cwd=str(REPO_ROOT))
        if proc.returncode != 0:
            raise RuntimeError(f"run {i} failed:\n{proc.stderr[-2000:]}")
        outputs.append(json.loads(proc.stdout.strip().splitlines()[-1]))
        print(f"run {i + 1}/{args.runs}: {len(outputs[-1])} reports extracted (fresh process, fresh weights load)")

    identical = all(o == outputs[0] for o in outputs[1:])
    diffs = []
    if not identical:
        for i, out in enumerate(outputs[1:], start=1):
            for a, b in zip(outputs[0], out):
                if a != b:
                    diffs.append({"run": i, "reportId": a["reportId"], "run0": a, "runN": b})

    result = {"modelDir": args.model_dir, "split": args.split, "runs": args.runs, "reportsPerRun": len(outputs[0]),
             "identicalAcrossAllRuns": identical, "diffs": diffs[:10],
             "method": "each run is a fresh `python` subprocess (fresh weights load from disk, fresh tokenizer, "
                       "no shared process state), unlike the prompted extractor which is a live sampled API call."}
    print(f"\nIdentical across {args.runs} independent fresh-process runs: {identical}")
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote {args.out}")
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main())
