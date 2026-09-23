"""Fail if anything under data/holdout has changed since the freeze recorded in FROZEN.json."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HOLD = Path(__file__).resolve().parents[2] / "data" / "holdout"


def main() -> int:
    frozen = json.loads((HOLD / "FROZEN.json").read_text(encoding="utf-8"))
    bad = []
    for rel, digest in frozen["sha256"].items():
        p = HOLD / rel
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            bad.append(rel)
    extra = [p.relative_to(HOLD).as_posix() for p in HOLD.rglob("*") if p.is_file() and "_src" not in p.parts and "review" not in p.parts
             and p.name != "FROZEN.json" and p.relative_to(HOLD).as_posix() not in frozen["sha256"]]
    if bad or extra:
        print("HOLDOUT CHANGED SINCE FREEZE:", bad, "unexpected files:", extra)
        return 1
    print(f"holdout intact: {len(frozen['sha256'])} files match the freeze of {frozen['frozenOn']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
