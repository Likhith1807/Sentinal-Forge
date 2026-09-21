"""Build the report corpus.

    python -m scripts.corpus.build --out data/corpus                 # template + LLM tiers
    python -m scripts.corpus.build --out data/corpus --skip-llm      # template tier only (offline)

Outputs under --out: reports/*.md, gold/*.gold.json, splits.json, leakage.json, manifest.json,
llm_cache.jsonl (rewrite results, so a rebuild is reproducible without calling the API again) and
verification/annotation_template.jsonl (blind sheet for the human second pass).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import CORPUS_VERSION, agreement, llm_rewrite, render, split
from .facts import sample
from .unsupported import sample_unsupported
from .vocab import BEHAVIOUR_IDS, REPO_ROOT

PROMPT_VERSION = "2"      # bump whenever llm_rewrite.PROMPT / temperature / try count changes
LLM_STYLES = list(llm_rewrite.STYLE_DESCRIPTIONS)


def _families(seed: int, per_behaviour: int, n_unsupported: int) -> list:
    out = []
    for behaviour in BEHAVIOUR_IDS:
        for i in range(1, per_behaviour + 1):
            out.append(sample(behaviour, i, random.Random(f"{seed}|{behaviour}|{i}")))
    for i in range(1, n_unsupported + 1):
        out.append(sample_unsupported(i, random.Random(f"{seed}|unsupported|{i}")))
    return out


def _cache_key(family_id: str, text: str, style: str, model: str) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}|{model}|{style}|{family_id}|{text}".encode()).hexdigest()


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    return {row["key"]: row["result"] for row in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l)}


def build(args: argparse.Namespace) -> dict:
    out = Path(args.out)
    (out / "reports").mkdir(parents=True, exist_ok=True)
    (out / "gold").mkdir(exist_ok=True)
    cache_path = out / "llm_cache.jsonl"
    cache = _load_cache(cache_path)

    families = _families(args.seed, args.families_per_behaviour, args.unsupported_families)
    records: list[dict] = []   # {id, family, tier, text, gold}
    jobs = []
    for k, f in enumerate(families, start=1):
        base_id, llm_id = f"SFC-{2 * k - 1:04d}", f"SFC-{2 * k:04d}"
        style = render.STYLES[(k - 1) % len(render.STYLES)]
        text, spans, structure = render.render(f, style, random.Random(f"{args.seed}|{f.family_id}|render"), base_id)
        gold = render.gold_record(f, base_id, f"data/corpus/reports/{base_id}.md", style, "template", spans, structure)
        records.append({"id": base_id, "family": f.family_id, "tier": "template", "text": text, "gold": gold})
        if not args.skip_llm:
            llm_style = LLM_STYLES[(k - 1) % len(LLM_STYLES)]
            jobs.append((f, llm_id, base_id, text, spans, llm_style, gold))

    stats = {"attempted": len(jobs), "accepted": 0, "failed": 0, "cached": 0, "tries": collections.Counter(),
             "failureReasons": collections.Counter()}
    if jobs:
        client = None if all(_cache_key(j[0].family_id, j[3], j[5], args.model) in cache for j in jobs) \
            else llm_rewrite.make_client()

        lock, done = threading.Lock(), [0]

        def run(job):
            f, llm_id, base_id, text, spans, llm_style, gold = job
            key = _cache_key(f.family_id, text, llm_style, args.model)
            if key in cache:
                return job, key, cache[key], True
            result = llm_rewrite.rewrite(
                client, text=text, spans=spans, thr_expr=render.thr_expression(f), win_expr=render.win_expression(f),
                entities=f.entities, meta=f.meta, supported=f.supported, style=llm_style,
                rng=random.Random(key), model=args.model)
            with lock:                      # durable the moment it completes, not in submission order
                with cache_path.open("a", encoding="utf-8", newline="\n") as log:
                    log.write(json.dumps({"key": key, "result": result}) + "\n")
                done[0] += 1
                print(f"  llm {done[0]}/{len(jobs)}: {f.family_id} "
                      f"{'ok' if result['ok'] else 'REJECTED ' + result['reason'][:60]}", flush=True)
            return job, key, result, False

        with ThreadPoolExecutor(max_workers=args.llm_workers) as pool:
            for job, key, result, was_cached in pool.map(run, jobs):
                f, llm_id, base_id, text, spans, llm_style, gold = job
                stats["cached"] += was_cached
                stats["tries"][result.get("tries", 0)] += 1
                if not result["ok"]:
                    stats["failed"] += 1
                    stats["failureReasons"][result["reason"].split(":")[0]] += 1
                    continue
                stats["accepted"] += 1
                kept_excluded = sorted({sp["field"] for sp in result["spans"] if sp["label"] == "excluded_field"})
                llm_gold = {**gold, "reportId": llm_id, "excludedFields": kept_excluded, "reportFile": f"data/corpus/reports/{llm_id}.md",
                            "style": result["style"], "tier": "llm-rewrite", "hasParamSection": False,
                            "spans": result["spans"], "derivedFrom": base_id, "llmModel": result["model"],
                            "llmTries": result["tries"], "fieldSpansAreVerbatimPreserved": True}
                records.append({"id": llm_id, "family": f.family_id, "tier": "llm-rewrite", "text": result["text"],
                                "gold": llm_gold})

    records.sort(key=lambda r: r["id"])
    for r in records:                       # every span must equal the text it labels
        for span in r["gold"]["spans"]:
            assert r["text"][span["start"]:span["end"]] == span["text"], (r["id"], span)
        (out / "reports" / f"{r['id']}.md").write_text(r["text"].rstrip("\n") + "\n", encoding="utf-8", newline="\n")
        (out / "gold" / f"{r['id']}.gold.json").write_text(json.dumps(r["gold"], indent=2) + "\n", encoding="utf-8", newline="\n")

    groups: dict[str, list[str]] = collections.defaultdict(list)
    for f in families:
        groups[f.behaviour or "unsupported"].append(f.family_id)
    family_split = split.family_split(groups, args.seed)
    split_of = {r["id"]: family_split[r["family"]] for r in records}
    (out / "splits.json").write_text(json.dumps(
        {"seed": args.seed, "fractions": split.FRACTIONS, "families": family_split, "reports": split_of}, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    leak = split.leakage_report({r["id"]: r["text"] for r in records}, split_of,
                                {r["id"]: r["family"] for r in records}, {r["id"]: r["tier"] for r in records},
                                {r["id"]: r["gold"]["behaviourId"] or "unsupported" for r in records},
                                max_cross_raw=args.max_cross_jaccard)
    (out / "leakage.json").write_text(json.dumps(leak, indent=2) + "\n", encoding="utf-8", newline="\n")

    sample_items = []
    if any(r["tier"] == "llm-rewrite" for r in records):
        sample_items = agreement.make_sample(out, args.verification_n, args.seed)
        (out / "verification").mkdir(exist_ok=True)
        (out / "verification" / "annotation_template.jsonl").write_text(
            "\n".join(json.dumps(x) for x in sample_items) + "\n", encoding="utf-8", newline="\n")

    def count(key):
        return dict(sorted(collections.Counter(key(r) for r in records).items(), key=lambda kv: str(kv[0])))

    manifest = {
        "corpusVersion": CORPUS_VERSION, "seed": args.seed, "model": None if args.skip_llm else args.model,
        "promptVersion": PROMPT_VERSION,
        "families": len(families), "reports": len(records),
        "byTier": count(lambda r: r["tier"]), "bySplit": count(lambda r: split_of[r["id"]]),
        "byBehaviour": count(lambda r: r["gold"]["behaviourId"] or "unsupported"),
        "byStyle": count(lambda r: r["gold"]["style"]),
        "bySplitAndBehaviour": count(lambda r: f"{split_of[r['id']]}/{r['gold']['behaviourId'] or 'unsupported'}"),
        "llm": {**{k: v for k, v in stats.items() if k not in ("tries", "failureReasons")},
                "triesHistogram": dict(sorted(stats["tries"].items())),
                "failureReasons": dict(stats["failureReasons"])},
        "leakageGatePassed": leak["gate"]["passed"],
        "verificationSampleSize": len(sample_items),
        "goldVerification": "every gold span equals the text at its offsets (asserted at build time); "
                            "no human second annotation has been run",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "corpus"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--families-per-behaviour", type=int, default=20)
    ap.add_argument("--unsupported-families", type=int, default=10)
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--llm-workers", type=int, default=4)
    ap.add_argument("--model", default=llm_rewrite.DEFAULT_MODEL)
    ap.add_argument("--max-cross-jaccard", type=float, default=0.5)
    ap.add_argument("--verification-n", type=int, default=30)
    manifest = build(ap.parse_args(argv))
    print(json.dumps({k: manifest[k] for k in ("families", "reports", "byTier", "bySplit", "llm", "leakageGatePassed")}, indent=2))
    return 0 if manifest["leakageGatePassed"] else 1


if __name__ == "__main__":
    sys.exit(main())
