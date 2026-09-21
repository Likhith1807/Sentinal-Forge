"""Score the existing classical (regex/keyword) extractor on the corpus, as a difficulty check.

    python -m scripts.corpus.eval_classical --corpus data/corpus --split test

The classical extractor was written against the 15 Phase 1 reports, which share one template and
whose "Analyst-confirmed detection parameters" section states field names in backticks. This is the
question it answers: is the new corpus harder than that, i.e. not solvable by reading one section?
It is NOT a claim about the project's extraction quality (that is Phase C, on this corpus).

Reported per slice so the reason for any drop is visible: tier, field-mention style, and whether a
"parameters" section exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from .vocab import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))
import classical_extractor  # noqa: E402  (imports schema_fields from the same directory)


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


def evaluate(corpus: Path, split: str) -> dict:
    splits = json.loads((corpus / "splits.json").read_text(encoding="utf-8"))["reports"]
    slices: dict[str, dict] = defaultdict(lambda: {"reports": 0, "tp": 0, "fp": 0, "fn": 0, "behaviourOk": 0,
                                                   "thresholdOk": 0, "thresholdTotal": 0})
    crashes = 0
    for gold_path in sorted((corpus / "gold").glob("*.gold.json")):
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        if splits[gold["reportId"]] != split or not gold["supported"]:
            continue                                   # supported behaviours only; abstention is scored separately
        text = (corpus / "reports" / f"{gold['reportId']}.md").read_text(encoding="utf-8")
        try:
            pred = classical_extractor.extract(text)
        except Exception:                              # noqa: BLE001 - a crash is itself a result
            crashes += 1
            continue
        gf = set(gold["requiredFields"]) | set(gold["policyFields"])
        pf = set(pred.requiredFields) | set(pred.policyFields)
        keys = ["all", f"tier={gold['tier']}", f"fieldMode={gold.get('fieldMode')}",
                f"paramSection={gold['hasParamSection']}", f"behaviour={gold['behaviourId']}"]
        for key in keys:
            s = slices[key]
            s["reports"] += 1
            s["tp"] += len(gf & pf); s["fp"] += len(pf - gf); s["fn"] += len(gf - pf)
            s["behaviourOk"] += pred.behaviourId == gold["behaviourId"]
            if gold["threshold"]:
                s["thresholdTotal"] += 1
                s["thresholdOk"] += (pred.threshold or {}) and next(iter(pred.threshold.values())) == next(iter(gold["threshold"].values()))
    out = {}
    for key, s in sorted(slices.items()):
        out[key] = {"reports": s["reports"], "behaviourAccuracy": round(s["behaviourOk"] / s["reports"], 3),
                    "fieldMicro": _prf(s["tp"], s["fp"], s["fn"]),
                    "thresholdExact": round(s["thresholdOk"] / s["thresholdTotal"], 3) if s["thresholdTotal"] else None}
    return {"extractor": "classical_extractor (regex/keyword, Phase 2)", "split": split, "crashes": crashes,
            "note": "Difficulty check only: supported-behaviour reports; abstention not scored.", "slices": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=REPO_ROOT / "data" / "corpus")
    ap.add_argument("--split", default="test", choices=["train", "dev", "test"])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    result = evaluate(args.corpus, args.split)
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
