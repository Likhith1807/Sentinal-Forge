"""Development probe (train+dev only): evidence-only reconciliation vs gold, listing every non-correct case."""
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from sentinelforge.pipeline import analyze  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402

splits = json.loads((REPO / "data/corpus/splits.json").read_text(encoding="utf-8"))["reports"]
want_splits = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else {"train", "dev"}
verbose = "-v" in sys.argv
tot = Counter()
for p in sorted((REPO / "data/corpus/gold").glob("*.json")):
    g = json.loads(p.read_text(encoding="utf-8"))
    if splits[g["reportId"]] not in want_splits:
        continue
    text = (REPO / g["reportFile"]).read_text(encoding="utf-8")
    a = analyze(text, None)
    ok = None
    if g["supported"]:
        thr = next(iter(g["threshold"].values())) if g.get("threshold") else None
        tw = g.get("timeWindow")
        want_secs = tw["amount"] * {"seconds": 1, "minutes": 60, "hours": 3600}[tw["unit"]] if tw else None
        c = a.compiled
        ok = c is not None and c["behaviourId"] == g["behaviourId"] and \
            (c.get("countThreshold") or c.get("distinctThreshold")) == thr and c.get("timeWindowSeconds") == want_secs
        cat = "correct" if ok else ("needs_review" if a.status == "needs_review" else "rejected" if a.compiled is None else "WRONG")
    else:
        cat = "silently-accepted" if a.compiled else "refused"
    tot[cat] += 1
    if cat not in ("correct", "refused"):
        print(g["reportId"], cat, g["behaviourId"], a.reconciliation.codes)
        if verbose:
            for r in a.reconciliation.reasons:
                print("     ", r.code, "-", r.message[:200], [e.quote for e in r.evidence][:2])
print(dict(tot))
