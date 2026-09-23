"""Development probe: how well does the deterministic condition finder recover gold on train+dev?
(Test split intentionally excluded - it is only read by the audit/evaluation scripts.)"""
import json, sys, glob, collections
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from sentinelforge.conditions import find_conditions
from sentinelforge.behaviours import BEHAVIOURS, canonical_id

splits = json.loads((REPO/"data/corpus/splits.json").read_text(encoding="utf-8"))["reports"]
splitsel = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else {"train", "dev"}
bad = collections.defaultdict(list); n = collections.Counter()
for p in sorted(glob.glob(str(REPO/"data/corpus/gold/*.json"))):
    g = json.loads(Path(p).read_text(encoding="utf-8"))
    if splits[g["reportId"]] not in splitsel: continue
    text = (REPO/g["reportFile"]).read_text(encoding="utf-8")
    r = find_conditions(text)
    b = BEHAVIOURS.get(g["behaviourId"]) if g["supported"] else None
    n["total"] += 1
    if not g["supported"]:
        top = [s for s in r.signals if not s.missing]
        n["unsup_flagged"] += 1 if (r.qualifiers or any(f.field is None for f in r.fields) or any(c.unknownObject for c in r.counts)) else 0
        n["unsup_signal_full"] += 1 if top else 0
        if top: bad["unsup-full-signal"].append((g["reportId"], [s.behaviourId for s in top]))
        continue
    sig = next(s for s in r.signals if s.behaviourId == b.id)
    if sig.missing: bad["gold-signal-missing"].append((g["reportId"], b.id, sig.missing))
    others = [s.behaviourId for s in r.signals if not s.missing and s.behaviourId != b.id]
    if others: bad["other-signal"].append((g["reportId"], b.id, others))
    if b.windowed:
        gv = next(iter(g["threshold"].values())); gw = g["timeWindow"]
        rc = [c for c in r.counts if c.ruleSentence and c.comparator == "gte"]
        vals = {(c.value, c.semantics) for c in rc}
        if vals != {(gv, b.count_semantics)}: bad["count"].append((g["reportId"], gv, b.count_semantics, [(c.value, c.semantics, c.eventKind, c.subject) for c in r.counts]))
        rw = [w for w in r.windows if w.ruleSentence and not w.approximate and w.cue]
        wv = {(w.amount, w.unit) for w in rw}
        if wv != {(float(gw["amount"]), gw["unit"])}: bad["window"].append((g["reportId"], gw, [(w.amount, w.unit, w.cue, w.ruleSentence, w.approximate, w.evidence.quote) for w in r.windows]))
    if r.qualifiers: bad["qualifier-on-supported"].append((g["reportId"], [(q.kind, q.evidence.quote) for q in r.qualifiers]))
    unk = [f.raw for f in r.fields if f.field is None]
    if unk: bad["unknown-field-on-supported"].append((g["reportId"], unk))
    gf = set(g["requiredFields"]) | set(g["policyFields"])
    mf = {f.field for f in r.fields if f.field}
    if mf and not mf <= gf | {"event_id"}: bad["extra-field"].append((g["reportId"], sorted(mf - gf)))
print(dict(n))
for k, v in bad.items():
    print(f"\n### {k}: {len(v)}")
    for row in v[:8]: print("  ", row)
