"""Compare classical, prompted-LLM and fine-tuned extractors on the corpus TEST split, with
bootstrap confidence intervals (roadmap item: "Compare four systems ... Report F1").

    python nlp/src/evaluate_corpus.py --model-dir nlp/models/roberta-base \
        --out experiments/results/phaseC_corpus_comparison.json

The **test split is read only here**, never during training or model selection (train_transformer.py
only ever touches train/dev). Every metric is computed once per report, then bootstrap-resampled
(reports, not fields) 2000 times to give a 95% CI — with 44 test reports the CIs are wide, and the
JSON says so rather than implying more precision than 44 reports can support.

The prompted-LLM system makes one real Groq call per report; `--skip-llm` skips it (e.g. if the
daily token quota is exhausted — this exact thing happened during the corpus build, see
docs/corpus.md) and reports classical + fine-tuned only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classical_extractor  # noqa: E402
import finetuned_extractor  # noqa: E402
from corpus_dataset import CORPUS_DIR  # noqa: E402

N_BOOTSTRAP = 2000
RNG_SEED = 7


def _fields(required, policy) -> set[str]:
    return set(required) | set(policy)


def _per_report_scores(gold: dict, pred) -> dict:
    """One report's raw counts/flags — bootstrap aggregates these, never re-derives from text."""
    pred_behaviour = pred.behaviourId if hasattr(pred, "behaviourId") else pred.get("behaviourId")
    pred_required = pred.requiredFields if hasattr(pred, "requiredFields") else pred.get("requiredFields", [])
    pred_policy = pred.policyFields if hasattr(pred, "policyFields") else pred.get("policyFields", [])
    pred_threshold = pred.threshold if hasattr(pred, "threshold") else pred.get("threshold")
    pred_window = pred.timeWindow if hasattr(pred, "timeWindow") else pred.get("timeWindow")

    gold_behaviour = gold["behaviourId"]  # None for unsupported
    gf, pf = _fields(gold["requiredFields"], gold["policyFields"]), _fields(pred_required, pred_policy)

    thr_ok = win_ok = None
    if gold["threshold"]:
        gv = next(iter(gold["threshold"].values()))
        pv = next(iter(pred_threshold.values())) if pred_threshold else None
        thr_ok = gv == pv
    if gold["timeWindow"]:
        gw, pw = gold["timeWindow"], pred_window
        win_ok = bool(pw) and gw["amount"] == pw.get("amount") and gw["unit"] == pw.get("unit")

    return {"reportId": gold["reportId"], "tier": gold["tier"], "supported": gold["supported"],
            "behaviourCorrect": pred_behaviour == gold_behaviour,
            "abstainedCorrectly": (not gold["supported"]) and pred_behaviour is None,
            "wronglyAbstained": gold["supported"] and pred_behaviour is None,
            "tp": len(gf & pf), "fp": len(pf - gf), "fn": len(gf - pf),
            "thresholdOk": thr_ok, "windowOk": win_ok}


def _aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    beh_acc = sum(r["behaviourCorrect"] for r in rows) / n
    tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
    p = tp / (tp + fp) if tp + fp else 1.0
    r_ = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * p * r_ / (p + r_) if p + r_ else 0.0
    supported = [r for r in rows if r["supported"]]
    unsupported = [r for r in rows if not r["supported"]]
    thr = [r["thresholdOk"] for r in rows if r["thresholdOk"] is not None]
    win = [r["windowOk"] for r in rows if r["windowOk"] is not None]
    return {
        "n": n, "behaviourAccuracy": beh_acc, "fieldPrecision": p, "fieldRecall": r_, "fieldF1": f1,
        "abstentionRecall": (sum(r["abstainedCorrectly"] for r in unsupported) / len(unsupported)) if unsupported else None,
        "falseAbstentionRate": (sum(r["wronglyAbstained"] for r in supported) / len(supported)) if supported else None,
        "thresholdExact": (sum(thr) / len(thr)) if thr else None,
        "windowExact": (sum(win) / len(win)) if win else None,
    }


def _bootstrap_ci(rows: list[dict], metric_key: str, rng: np.random.Generator) -> tuple[float, float] | None:
    values = []
    idx = np.arange(len(rows))
    for _ in range(N_BOOTSTRAP):
        sample = [rows[i] for i in rng.choice(idx, size=len(idx), replace=True)]
        agg = _aggregate(sample)
        if agg[metric_key] is not None:
            values.append(agg[metric_key])
    if len(values) < N_BOOTSTRAP * 0.5:     # metric undefined on most resamples (tiny subgroup)
        return None
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))


def run_system(name: str, predict_fn, golds: list[dict], rng: np.random.Generator, retries: int = 2) -> dict:
    """A predict_fn exception is UNAVAILABLE, not a scored answer — it must never be folded into
    "wrongly abstained" or any other metric, or a system that merely errors (e.g. an exhausted API
    quota) would be scored as if it had confidently answered wrong. Errored reports are excluded
    from every metric and listed separately, so a reader can see exactly what wasn't scored and why.
    """
    rows, errored = [], []
    started = time.time()
    for gold in golds:
        text = (CORPUS_DIR / "reports" / f"{gold['reportId']}.md").read_text(encoding="utf-8")
        last_exc = None
        for attempt in range(retries + 1):
            try:
                pred = predict_fn(text)
                rows.append(_per_report_scores(gold, pred))
                break
            except Exception as exc:  # noqa: BLE001 - classify below, never silently scored
                last_exc = exc
                if attempt < retries:
                    time.sleep(3.0)
        else:
            errored.append({"reportId": gold["reportId"], "error": type(last_exc).__name__, "message": str(last_exc)[:200]})
    metrics = _aggregate(rows) if rows else {}
    cis = {f"{k}CI95": _bootstrap_ci(rows, k, np.random.default_rng(RNG_SEED))
          for k in ("behaviourAccuracy", "fieldF1", "abstentionRecall", "thresholdExact", "windowExact")} if rows else {}
    return {"system": name, "requested": len(golds), "scored": len(rows), "unavailable": errored,
            "seconds": round(time.time() - started, 1), **metrics, **cis, "perReport": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    ap.add_argument("--split", default="test")
    ap.add_argument("--model-dir", default=str(finetuned_extractor.DEFAULT_MODEL_DIR))
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    splits = json.loads((args.corpus / "splits.json").read_text(encoding="utf-8"))["reports"]
    golds = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((args.corpus / "gold").glob("*.gold.json"))]
    golds = [g for g in golds if splits[g["reportId"]] == args.split]
    print(f"{len(golds)} reports in split={args.split}")

    rng = np.random.default_rng(RNG_SEED)
    results = {"classical": run_system("classical", classical_extractor.extract, golds, rng)}
    if not args.skip_llm:
        import transformer_extractor
        results["transformer-prompted"] = run_system("transformer-prompted", transformer_extractor.extract, golds, rng)
    results["fine-tuned"] = run_system("fine-tuned", lambda t: finetuned_extractor.extract(t, args.model_dir), golds, rng)
    if not args.skip_llm:
        import hybrid_extractor
        results["hybrid"] = run_system("hybrid", lambda t: hybrid_extractor.extract(t, args.model_dir), golds, rng)

    summary = {name: {k: v for k, v in r.items() if k != "perReport"} for name, r in results.items()}
    out = {"split": args.split, "n": len(golds), "bootstrapResamples": N_BOOTSTRAP,
          "note": f"{len(golds)} test reports; CIs are wide by construction and should be read as such.",
          "systems": results}
    text = json.dumps(out, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
