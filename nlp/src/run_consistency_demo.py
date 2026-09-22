"""Live demonstration of consistency_extractor.measure_variance on a real sample of test reports.

    python nlp/src/run_consistency_demo.py --n 10 --model openai/gpt-oss-20b \
        --out experiments/results/phaseC_consistency_variance.json

Deliberately a small sample (default 10 reports x 3 samples = 30 calls): this exists to measure
whether self-consistency voting actually reduces the documented run-to-run variance, not to
re-run the full corpus comparison a second time under a different mechanism.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from consistency_extractor import extract as consistency_extract, measure_variance  # noqa: E402
from corpus_dataset import CORPUS_DIR  # noqa: E402
import transformer_extractor  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--n-samples", type=int, default=3)
    ap.add_argument("--model", default=transformer_extractor.DEFAULT_MODEL)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    splits = json.loads((CORPUS_DIR / "splits.json").read_text(encoding="utf-8"))["reports"]
    golds = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((CORPUS_DIR / "gold").glob("*.gold.json"))]
    test_supported = [g for g in golds if splits[g["reportId"]] == "test" and g["supported"]]
    random.Random(args.seed).shuffle(test_supported)
    sample = test_supported[:args.n]
    texts = {g["reportId"]: (CORPUS_DIR / "reports" / f"{g['reportId']}.md").read_text(encoding="utf-8") for g in sample}
    print(f"measuring raw variance over {len(texts)} reports x {args.n_samples} samples, model={args.model}")

    variance = measure_variance(texts, n_samples=args.n_samples, model=args.model)
    if variance["behaviourAgreementRate"] is None:
        print(f"raw (unvoted) agreement: undefined — all {len(texts)} reports had fewer than 2 "
             f"successful samples (see reportsSkipped); this is itself a reliability data point, not a crash")
    else:
        print(f"raw (unvoted) agreement: behaviourId {variance['behaviourAgreementRate']:.3f}, "
             f"field-set {variance['fieldSetAgreementRate']:.3f}")

    # Now check the VOTED answer against gold directly (does voting fix the disagreements, not just measure them).
    gold_by_id = {g["reportId"]: g for g in sample}
    voted_correct = 0
    voted_rows = []
    for report_id, text in texts.items():
        # Reuses the same samples measure_variance already drew is not possible without threading
        # them through; a fresh vote call is a fresh (and fair) draw of n_samples more calls.
        voted = consistency_extract(text, n_samples=args.n_samples, model=args.model)
        gold = gold_by_id[report_id]
        correct = voted["behaviourId"] == gold["behaviourId"]
        voted_correct += correct
        voted_rows.append({"reportId": report_id, "votedBehaviourId": voted["behaviourId"],
                           "goldBehaviourId": gold["behaviourId"], "correct": correct,
                           "behaviourAgreement": voted["agreement"]["behaviourId"],
                           "nFailed": voted["nFailed"], "failures": voted.get("failures", [])})
    voted_accuracy = voted_correct / len(texts)
    fully_failed = sum(r["nFailed"] == args.n_samples for r in voted_rows)
    print(f"voted-answer behaviour accuracy vs gold: {voted_accuracy:.3f} ({fully_failed} report(s) had every sample fail)")

    result = {"n": len(texts), "nSamples": args.n_samples, "model": args.model,
             "rawVariance": {k: v for k, v in variance.items() if k != "perReport"},
             "rawVariancePerReport": variance["perReport"],
             "votedBehaviourAccuracy": voted_accuracy, "votedPerReport": voted_rows}
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
