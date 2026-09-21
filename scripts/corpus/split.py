"""Family-aware train/dev/test split and a measured near-duplicate leakage check.

A "family" is one incident rendered several ways (template report + LLM rewrite), so its members
are near-duplicates by construction and must never straddle splits. The split is therefore made
over families, stratified by behaviour. Leakage is then *measured*, not assumed:

  * raw 5-gram Jaccard between every cross-split report pair (near-duplicate detection; gated);
  * the same on a normalised text (numbers, identifiers and hostnames masked), which measures
    *template* similarity -- the risk that a model passes the test split by recognising report
    skeletons rather than by extracting. That number is reported, not gated, because a
    template-driven corpus is expected to have some; docs/corpus.md discusses it.
"""
from __future__ import annotations

import itertools
import random
import re
import statistics
from collections import defaultdict

from .vocab import NUMBER_WORDS

FRACTIONS = (0.6, 0.2, 0.2)
SPLITS = ("train", "dev", "test")
_NUMBER_WORD_RE = re.compile(r"\b(?:" + "|".join(NUMBER_WORDS.values()) + r")\b")


def family_split(groups: dict[str, list[str]], seed: int) -> dict[str, str]:
    """``groups`` maps a stratum (behaviour) to its family ids; returns ``family_id -> split``."""
    assignment: dict[str, str] = {}
    for stratum in sorted(groups):
        families = sorted(groups[stratum])
        random.Random(f"{seed}|{stratum}").shuffle(families)
        n = len(families)
        n_dev = max(1, round(n * FRACTIONS[1]))
        n_test = max(1, round(n * FRACTIONS[2]))
        for i, family in enumerate(families):
            assignment[family] = "train" if i < n - n_dev - n_test else ("dev" if i < n - n_test else "test")
    return assignment


def shingles(text: str, n: int = 5) -> set[str]:
    tokens = re.findall(r"[a-z0-9_.\-/]+", text.lower())
    return {" ".join(tokens[i:i + n]) for i in range(max(0, len(tokens) - n + 1))}


def normalise(text: str) -> str:
    """Mask everything that varies per instance so only the skeleton remains."""
    text = re.sub(r"\bINC-\d+\b", "<ticket>", text)
    text = re.sub(r"\b[A-Za-z]+(?:-[A-Za-z]+)*-\d+\b", "<host>", text)
    text = re.sub(r"\b\d+(?:[:.]\d+)*\b", "<n>", text)
    text = _NUMBER_WORD_RE.sub("<n>", text.lower())
    text = re.sub(r"\b[a-z]+\.[a-z]+\b", "<acct>", text)
    return text


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def leakage_report(texts: dict[str, str], split_of: dict[str, str], family_of: dict[str, str],
                   tier_of: dict[str, str], behaviour_of: dict[str, str], max_cross_raw: float = 0.5) -> dict:
    raw = {r: shingles(t) for r, t in texts.items()}
    norm = {r: shingles(normalise(t)) for r, t in texts.items()}

    cross, within = [], []
    same_behaviour: list[tuple] = []          # cross-split pairs about the SAME behaviour: the real template-leak risk
    by_tiers: dict[str, list[float]] = defaultdict(list)
    for a, b in itertools.combinations(sorted(texts), 2):
        pair = (a, b, jaccard(raw[a], raw[b]), jaccard(norm[a], norm[b]))
        if family_of[a] == family_of[b]:
            within.append(pair)
        elif split_of[a] != split_of[b]:
            cross.append(pair)
            if behaviour_of[a] == behaviour_of[b]:
                same_behaviour.append(pair)
                by_tiers["/".join(sorted((tier_of[a], tier_of[b])))].append(pair[3])

    def summary(values: list[float]) -> dict:
        return {"pairs": len(values), "max": round(max(values), 3) if values else None,
                "mean": round(statistics.mean(values), 3) if values else None,
                "p95": round(sorted(values)[int(0.95 * (len(values) - 1))], 3) if values else None}

    top = sorted(cross, key=lambda p: -p[2])[:5]
    return {
        "gate": {"maxCrossSplitRawJaccard": max_cross_raw,
                 "passed": all(p[2] < max_cross_raw for p in cross)},
        "familiesStraddlingSplits": sorted({family_of[r] for r in texts
                                            if len({split_of[x] for x in texts if family_of[x] == family_of[r]}) > 1}),
        "exactDuplicateTexts": len(texts) - len(set(texts.values())),
        "crossSplitRaw": summary([p[2] for p in cross]),
        "crossSplitNormalised": summary([p[3] for p in cross]),
        "crossSplitSameBehaviourRaw": summary([p[2] for p in same_behaviour]),
        "crossSplitSameBehaviourNormalised": summary([p[3] for p in same_behaviour]),
        "crossSplitSameBehaviourNormalisedByTier": {k: summary(v) for k, v in sorted(by_tiers.items())},
        "withinFamilyRaw": summary([p[2] for p in within]),
        "withinFamilyNormalised": summary([p[3] for p in within]),
        "mostSimilarCrossSplitPairs": [{"a": a, "b": b, "rawJaccard": round(r, 3), "normalisedJaccard": round(n, 3)}
                                       for a, b, r, n in top],
    }
