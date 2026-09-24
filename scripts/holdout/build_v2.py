"""Build the frozen holdout v2: data/holdout_v2/{reports,gold}/ + manifest.json + FROZEN.json.

    python scripts/holdout/build.py

Deterministic: same sources -> byte-identical outputs. After the freeze commit nothing under data/holdout may
change; scripts/holdout/verify_frozen.py checks the recorded SHA-256 of every file (run by CI).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))

from entries_v2_nocompile import NOCOMPILE  # noqa: E402
from entries_v2_real import REAL  # noqa: E402
from entries_v2_supported import SUPPORTED  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402

OUT = REPO / "data" / "holdout_v2"
SRC = REPO / "data" / "holdout" / "_src"  # raw public advisories are shared with v1
BUILD_DATE = "2026-09-24"


def locate(text: str, quote: str, what: str, rid: str) -> tuple[int, int]:
    n = text.count(quote)
    if n != 1:
        raise SystemExit(f"{rid}: {what} quote {quote!r} occurs {n} times (must be exactly once)")
    s = text.index(quote)
    return s, s + len(quote)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> None:
    (OUT / "reports").mkdir(parents=True, exist_ok=True)
    (OUT / "gold").mkdir(parents=True, exist_ok=True)
    # start clean: a report dropped or renamed in the sources must not survive in the frozen set
    for stale in list((OUT / "reports").glob("*")) + list((OUT / "gold").glob("*")):
        stale.unlink()
    manifest, files = [], {}

    def emit(rid: str, text: str, gold: dict, meta: dict) -> None:
        text = text.strip("\n") + "\n"
        (OUT / "reports" / f"{rid}.md").write_text(text, encoding="utf-8", newline="\n")
        gold = {"reportId": rid, "reportSha256": sha(text.encode("utf-8")), **gold}
        (OUT / "gold" / f"{rid}.gold.json").write_text(json.dumps(gold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        manifest.append({"reportId": rid, "expect": gold["expect"], **meta})

    for e in SUPPORTED:
        beh = B.BEHAVIOURS[e["behaviour"]]
        text = e["text"].strip("\n") + "\n"
        gold = {"expect": "compiled", "behaviourId": beh.id, "fields": [f for f in beh.log_fields if f != "event_id"] + list(beh.policy_fields)}
        if beh.windowed:
            cq, cv = e["count"]
            wq, ws = e["window"]
            cs, ce = locate(text, cq, "count", e["id"])
            wsx, wex = locate(text, wq, "window", e["id"])
            gold["count"] = {"value": cv, "semantics": beh.count_semantics, "evidence": {"start": cs, "end": ce, "quote": cq}}
            gold["window"] = {"seconds": ws, "evidence": {"start": wsx, "end": wex, "quote": wq}}
        emit(e["id"], text, gold, {"source": "authored", "style": e["style"], "family": f"HF-{e['id']}", "behaviourId": beh.id})

    for e in NOCOMPILE:
        emit(e["id"], e["text"], {"expect": "no_compile", "reasonClass": e["reasonClass"], "fields": []},
             {"source": "authored", "style": e["style"], "family": f"HF-{e['id']}", "reasonClass": e["reasonClass"]})

    for e in REAL:
        lines = (SRC / e["src"]["file"]).read_text(encoding="utf-8").splitlines()
        blob = "\n".join(lines)
        s = blob.index(e["start"])
        end = blob.index(e["end"], s) + len(e["end"])
        passage = blob[s:end]
        emit(e["id"], passage, {"expect": "no_compile", "reasonClass": e["reasonClass"], "fields": [], "why": e["why"]},
             {"source": "real-public", "style": "advisory-excerpt", "family": f"HF-{e['id']}", "reasonClass": e["reasonClass"],
              "citation": {"title": e["src"]["title"], "url": e["src"]["url"], "retrieved": BUILD_DATE,
                           "license": "US Government work (public domain), CISA/FBI joint advisory"}})

    manifest.sort(key=lambda m: m["reportId"])
    (OUT / "manifest.json").write_text(json.dumps({
        "name": "SENTINEL Forge frozen holdout v2", "version": "1.0.0", "frozenOn": BUILD_DATE, "reports": len(manifest),
        "counts": {k: sum(1 for m in manifest if m["source"] == k) for k in ("authored", "real-public")},
        "byExpectation": {k: sum(1 for m in manifest if m["expect"] == k) for k in ("compiled", "no_compile")},
        "labelling": "single annotator (the author); a second independent review is tracked in review/ and is NOT complete unless review/RESULT.json says so",
        "separation": "No report shares a template, family, or rewriting model with data/corpus (train/dev/test). Authored AFTER the v1 failure analysis and the parser fixes it drove, before the parser was ever run on these reports; never tuned against.",
        "entries": manifest}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for p in sorted(OUT.rglob("*")):
        if p.is_file() and "_src" not in p.parts and p.name not in ("FROZEN.json",) and "review" not in p.parts:
            files[p.relative_to(OUT).as_posix()] = sha(p.read_bytes())
    (OUT / "FROZEN.json").write_text(json.dumps({"frozenOn": BUILD_DATE, "sha256": files}, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(manifest)} reports frozen ({sum(1 for m in manifest if m['expect'] == 'compiled')} supported, "
          f"{sum(1 for m in manifest if m['expect'] == 'no_compile')} must-not-compile)")


if __name__ == "__main__":
    main()
