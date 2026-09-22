"""Two ablations on the fine-tuned extractor, evaluated on the same frozen test split.

    python nlp/src/ablate_corpus.py --out experiments/results/phaseC_ablations.json

1. **Encoder choice**: generic `roberta-base` vs the cybersecurity-domain `ehsanaghaei/SecureBERT`
   (both RoBERTa-architecture, same head code) — does domain-adapted pretraining help on a small
   fine-tuning set?
2. **Training-tier**: train on the template tier only vs template + LLM-rewrite, evaluated
   separately on the test split's template-tier and LLM-rewrite-tier reports — does the guarded
   LLM-rewrite tier (docs/corpus.md) actually improve generalisation to reworded reports, or does
   it just add noise?

Trains all checkpoints itself (each is a few minutes on a GPU); does not reuse a checkpoint you
may have already trained by hand under a different config, to keep the comparison controlled.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_transformer  # noqa: E402
import finetuned_extractor  # noqa: E402
from corpus_dataset import CORPUS_DIR  # noqa: E402
from evaluate_corpus import run_system  # noqa: E402
import numpy as np  # noqa: E402


def _train(out_dir: Path, encoder: str, tiers: list[str] | None, seed: int) -> None:
    args = ["--encoder", encoder, "--out", str(out_dir), "--seed", str(seed)]
    if tiers:
        args += ["--train-tiers", *tiers]
    train_transformer.main(args)


def _eval(model_dir: Path, golds: list[dict], name: str) -> dict:
    rng = np.random.default_rng(7)
    result = run_system(name, lambda t: finetuned_extractor.extract(t, model_dir), golds, rng)
    return {k: v for k, v in result.items() if k != "perReport"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models-dir", default="nlp/models")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    models_dir = Path(args.models_dir)

    splits = json.loads((CORPUS_DIR / "splits.json").read_text(encoding="utf-8"))["reports"]
    golds = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((CORPUS_DIR / "gold").glob("*.gold.json"))]
    test_golds = [g for g in golds if splits[g["reportId"]] == "test"]
    test_template = [g for g in test_golds if g["tier"] == "template"]
    test_llm = [g for g in test_golds if g["tier"] == "llm-rewrite"]
    print(f"test split: {len(test_golds)} total ({len(test_template)} template, {len(test_llm)} llm-rewrite)")

    results: dict[str, dict] = {}

    # --- Ablation 1: encoder choice ---------------------------------------------------------
    securebert_dir = models_dir / "securebert"
    print("\n=== training SecureBERT (ablation 1) ===")
    _train(securebert_dir, "ehsanaghaei/SecureBERT", None, args.seed)
    results["encoder=securebert (test, all tiers)"] = _eval(securebert_dir, test_golds, "securebert")

    roberta_dir = models_dir / "roberta-base"
    if not (roberta_dir / "model.pt").exists():
        print("\n=== training roberta-base (needed for ablation 1 and 2 baseline) ===")
        _train(roberta_dir, "roberta-base", None, args.seed)
    results["encoder=roberta-base (test, all tiers)"] = _eval(roberta_dir, test_golds, "roberta-base")

    # --- Ablation 2: training-tier ---------------------------------------------------------
    template_only_dir = models_dir / "roberta-base-template-only"
    print("\n=== training roberta-base on template-tier-only data (ablation 2) ===")
    _train(template_only_dir, "roberta-base", ["template"], args.seed)

    for tag, model_dir in (("train=all-tiers", roberta_dir), ("train=template-only", template_only_dir)):
        results[f"{tag} | eval=test-template"] = _eval(model_dir, test_template, f"{tag}/template")
        if test_llm:
            results[f"{tag} | eval=test-llm-rewrite"] = _eval(model_dir, test_llm, f"{tag}/llm-rewrite")

    print("\n=== summary ===")
    for name, r in results.items():
        print(f"{name:45s} n={r['n']:3d} behAcc={r['behaviourAccuracy']:.3f} fieldF1={r['fieldF1']:.3f} "
             f"thr={r['thresholdExact']} win={r['windowExact']}")

    if args.out:
        args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
