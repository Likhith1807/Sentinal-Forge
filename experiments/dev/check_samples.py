"""How does the evidence pipeline (no model) treat each bundled sample report?"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from sentinelforge.pipeline import analyze  # noqa: E402

m = json.loads((REPO / "data/demo/reports/manifest.json").read_text(encoding="utf-8"))
for s in m["samples"]:
    t = (REPO / "data/demo/reports" / s["file"]).read_text(encoding="utf-8")
    a = analyze(t, None)
    want = {"compiled": "compiled", "rejected": "rejected", "needs_review": "needs_review"}[s["expect"]]
    flag = "OK " if a.status == want and (want != "compiled" or a.behaviourId == s["behaviour"]) else "BAD"
    print(f"{flag} {s['id']:22s} want={s['expect']:13s} got={a.status:12s} {a.behaviourId} {a.reconciliation.codes} "
          f"win={(a.compiled or {}).get('timeWindowSeconds')} n={(a.compiled or {}).get('countThreshold') or (a.compiled or {}).get('distinctThreshold')}")
