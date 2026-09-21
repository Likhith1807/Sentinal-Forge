"""Small gold-consistency probe: does a capable LLM extractor agree with the corpus gold?

    python -m scripts.corpus.probe_llm --corpus data/corpus --split test --n 16

This is NOT an extraction evaluation (that is Phase C, with proper sampling and repeated runs). It exists
to catch gold bugs: gold is exact by construction, but a wrong construction would be exact-but-wrong, and the
only way to notice is for an independent reader to disagree. Every disagreement is printed for inspection.
A capable model agreeing supports "the gold is consistent with the text"; it does not prove gold correctness
(a human pass is still needed) and disagreement may be the model's error, not the gold's.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

from .vocab import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))
import transformer_extractor  # noqa: E402

UNIT = {"seconds": 1, "minutes": 60, "hours": 3600}


def _secs(w):
    return None if not w else round(w["amount"] * UNIT.get(w.get("unit", "seconds"), 1))


def probe(corpus: Path, split: str, n: int, seed: int, pause: float) -> dict:
    splits = json.loads((corpus / "splits.json").read_text(encoding="utf-8"))["reports"]
    golds = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((corpus / "gold").glob("*.gold.json"))]
    pool = [g for g in golds if splits[g["reportId"]] == split and g["supported"]]
    random.Random(seed).shuffle(pool)
    rows, errors = [], Counter()
    for g in pool[:n]:
        text = (corpus / "reports" / f"{g['reportId']}.md").read_text(encoding="utf-8")
        try:
            pred = transformer_extractor.extract(text)
        except Exception as exc:  # noqa: BLE001 - provider errors are recorded, not fatal
            errors[type(exc).__name__] += 1
            time.sleep(pause)
            continue
        gf, pf = set(g["requiredFields"]) | set(g["policyFields"]), set(pred["requiredFields"]) | set(pred["policyFields"])
        thr_g = next(iter(g["threshold"].values())) if g["threshold"] else None
        thr_p = next(iter(pred["threshold"].values())) if pred.get("threshold") else None
        rows.append({"reportId": g["reportId"], "tier": g["tier"], "behaviourOk": pred["behaviourId"] == g["behaviourId"],
                     "fieldsExact": gf == pf, "missing": sorted(gf - pf), "extra": sorted(pf - gf),
                     "thresholdOk": thr_g == thr_p, "windowOk": _secs(g["timeWindow"]) == _secs(pred.get("timeWindow")),
                     "gold": {"threshold": g["threshold"], "timeWindow": g["timeWindow"], "form": g.get("thresholdForm")},
                     "pred": {"behaviourId": pred["behaviourId"], "threshold": pred.get("threshold"),
                              "timeWindow": pred.get("timeWindow")}})
        time.sleep(pause)
    k = len(rows)
    rate = lambda key: round(sum(r[key] for r in rows) / k, 3) if k else None  # noqa: E731
    return {"model": transformer_extractor.DEFAULT_MODEL, "split": split, "requested": n, "scored": k,
            "providerErrors": dict(errors), "behaviourAccuracy": rate("behaviourOk"), "fieldsExact": rate("fieldsExact"),
            "thresholdExact": rate("thresholdOk"), "windowExact": rate("windowOk"),
            "disagreements": [r for r in rows if not (r["behaviourOk"] and r["fieldsExact"] and r["thresholdOk"] and r["windowOk"])],
            "note": "Gold-consistency probe on a small random sample; not an extraction evaluation."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=REPO_ROOT / "data" / "corpus")
    ap.add_argument("--split", default="test")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--pause", type=float, default=6.0, help="seconds between calls (per-minute token caps)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    result = probe(args.corpus, args.split, args.n, args.seed, args.pause)
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
