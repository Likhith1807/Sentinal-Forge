"""Human verification sample and inter-annotator agreement.

The corpus gold is exact *by construction* for the template tier, but that says nothing about
whether a human reading the text reaches the same conclusion, and the LLM-rewrite tier's gold is
inherited from the template it was rewritten from. The only honest way to check either is an
independent human pass. This module produces the blind annotation sheet and scores it.

    python -m scripts.corpus.agreement sample  --corpus data/corpus --n 30
    # ... a human fills data/corpus/verification/annotation_template.jsonl WITHOUT looking at gold/ ...
    python -m scripts.corpus.agreement score   --corpus data/corpus --annotations <filled file>

No annotator agreement figure exists until someone runs the second step; docs/corpus.md says so.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600}
UNSUPPORTED = "unsupported"


def load_gold(corpus: Path) -> dict[str, dict]:
    return {p.name.removesuffix(".gold.json"): json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((corpus / "gold").glob("*.gold.json"))}


def make_sample(corpus: Path, n: int, seed: int) -> list[dict]:
    """Stratified blind sample from the TEST split: balanced over behaviour and tier."""
    gold = load_gold(corpus)
    splits = json.loads((corpus / "splits.json").read_text(encoding="utf-8"))["reports"]
    strata: dict[tuple, list[str]] = defaultdict(list)
    for rid, g in gold.items():
        if splits[rid] == "test":
            strata[(g["behaviourId"] or UNSUPPORTED, g["tier"])].append(rid)
    rng = random.Random(seed)
    for ids in strata.values():
        rng.shuffle(ids)
    chosen: list[str] = []
    while len(chosen) < n and any(strata.values()):
        for key in sorted(strata):
            if strata[key] and len(chosen) < n:
                chosen.append(strata[key].pop())
    rng.shuffle(chosen)
    return [{"reportId": rid, "reportFile": gold[rid]["reportFile"],
             "annotation": {"supported": None, "behaviourId": None, "requiredFields": [], "policyFields": [],
                            "excludedFields": [], "threshold": None, "timeWindow": None}} for rid in chosen]


def _seconds(window: dict | None) -> int | None:
    return None if not window else round(window["amount"] * UNIT_SECONDS[window["unit"]])


def _fields(record: dict) -> set[str]:
    return set(record.get("requiredFields", [])) | set(record.get("policyFields", []))


def _label(record: dict) -> str:
    return record["behaviourId"] if record.get("supported", True) and record.get("behaviourId") else UNSUPPORTED


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    n = len(a)
    if not n:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if expected == 1 else round((observed - expected) / (1 - expected), 3)


def score(gold: dict[str, dict], annotations: list[dict]) -> dict:
    rows, tp = [], [0, 0, 0]
    for item in annotations:
        g, a = gold[item["reportId"]], item["annotation"]
        if a.get("supported") is None:
            raise ValueError(f"{item['reportId']}: annotation is blank; annotate every report before scoring")
        a = {**a, "behaviourId": a.get("behaviourId") if a["supported"] else None}
        gf, af = _fields(g), _fields(a)
        tp[0] += len(gf & af); tp[1] += len(af - gf); tp[2] += len(gf - af)
        thr_g = next(iter(g["threshold"].values())) if g.get("threshold") else None
        thr_a = next(iter(a["threshold"].values())) if a.get("threshold") else None
        rows.append({"reportId": item["reportId"], "tier": g["tier"], "goldLabel": _label(g), "annLabel": _label(a),
                     "thresholdMatch": thr_g == thr_a, "windowMatch": _seconds(g.get("timeWindow")) == _seconds(a.get("timeWindow")),
                     "fieldsExact": gf == af})
    p = tp[0] / (tp[0] + tp[1]) if tp[0] + tp[1] else 1.0
    r = tp[0] / (tp[0] + tp[2]) if tp[0] + tp[2] else 1.0
    by_tier = {}
    for tier in sorted({row["tier"] for row in rows}):
        sub = [row for row in rows if row["tier"] == tier]
        by_tier[tier] = {"reports": len(sub), "labelAgreement": round(sum(x["goldLabel"] == x["annLabel"] for x in sub) / len(sub), 3),
                         "fieldsExact": round(sum(x["fieldsExact"] for x in sub) / len(sub), 3)}
    return {
        "reports": len(rows),
        "labelKappa": cohen_kappa([x["goldLabel"] for x in rows], [x["annLabel"] for x in rows]),
        "labelAgreement": round(sum(x["goldLabel"] == x["annLabel"] for x in rows) / len(rows), 3),
        "fieldMicroPrecision": round(p, 3), "fieldMicroRecall": round(r, 3),
        "fieldMicroF1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
        "thresholdExactMatch": round(sum(x["thresholdMatch"] for x in rows) / len(rows), 3),
        "windowExactMatch": round(sum(x["windowMatch"] for x in rows) / len(rows), 3),
        "byTier": by_tier,
        "disagreements": [x for x in rows if x["goldLabel"] != x["annLabel"] or not x["fieldsExact"]
                          or not x["thresholdMatch"] or not x["windowMatch"]],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample"); s.add_argument("--corpus", type=Path, required=True)
    s.add_argument("--n", type=int, default=30); s.add_argument("--seed", type=int, default=7)
    c = sub.add_parser("score"); c.add_argument("--corpus", type=Path, required=True)
    c.add_argument("--annotations", type=Path, required=True); c.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "sample":
        sample = make_sample(args.corpus, args.n, args.seed)
        out = args.corpus / "verification"
        out.mkdir(exist_ok=True)
        (out / "annotation_template.jsonl").write_text(
            "\n".join(json.dumps(x) for x in sample) + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote {len(sample)} blind items to {out / 'annotation_template.jsonl'}")
        return 0
    annotations = [json.loads(line) for line in args.annotations.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = score(load_gold(args.corpus), annotations)
    text = json.dumps(result, indent=2)
    (args.out.write_text(text + "\n", encoding="utf-8") if args.out else print(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
