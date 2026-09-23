"""Evaluation screen data: archived measurements, each labelled with what it is and how to reproduce it.

Every entry says where its numbers came from (a committed JSON file), what dataset they were measured on,
and whether the data was tuned against ("regression") or held out ("frozen holdout"). The UI shows this
provenance next to the numbers; nothing here is computed live.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS = REPO_ROOT / "experiments" / "results"
INDEX = RESULTS / "evaluation_index.json"


def _load(name: str):
    p = RESULTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def evaluation_payload() -> dict:
    if INDEX.exists():
        index = json.loads(INDEX.read_text(encoding="utf-8"))
    else:
        index = {"sections": []}
    out = {"generatedFrom": "experiments/results/*.json (archived; not computed live)", "sections": []}
    for sec in index["sections"]:
        data = {k: _load(f) for k, f in sec.get("files", {}).items()}
        out["sections"].append({**{k: v for k, v in sec.items() if k != "files"}, "data": data,
                                "missing": [f for f in sec.get("files", {}).values() if not (RESULTS / f).exists()]})
    return out
