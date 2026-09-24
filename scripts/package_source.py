"""Build (or check) a clean, source-only release archive from the files git tracks.

    python scripts/package_source.py            # dist/sentinel-forge-<version>-src.zip + MANIFEST
    python scripts/package_source.py --check    # fail if a forbidden file is tracked (used in CI)

"Source-only" means: code, tests, docs, the small committed datasets and results - and none of: secrets, model
checkpoints, build output, runtime state, generated multi-GB data, slide decks, editor files, or anything over
5 MB. The archive is deterministic (sorted paths, fixed timestamps), so two builds of one commit are byte-identical.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from sentinelforge import __version__  # noqa: E402

FORBIDDEN = [".env", "*.env.local", "*.pt", "*.pth", "*.bin", "*.safetensors", "*.parquet", "*.pyc",
             "target/*", "project/target/*", "var/*", ".tools/*", "nlp/models/*/*", "data/generated/*", "data/processed/*",
             "*/__pycache__/*", ".idea/*", ".metals/*", ".bloop/*", "*.db", "*.sqlite", "dist/*"]
# Tracked in the repository but deliberately left out of the release archive (course-work slide decks and their build scripts)
EXCLUDED_FROM_ARCHIVE = ["course/*", "*.pptx", "*.potx"]
# committed on purpose although they look like data: the LLM response cache that makes the corpus rebuild reproducible
MAX_BYTES = 5 * 1024 * 1024
ALLOW_LARGE = {"data/corpus/llm_cache.jsonl"}


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.split("\0") if p)


def problems(files: list[str]) -> list[str]:
    bad = []
    for f in files:
        if any(fnmatch.fnmatch(f, pat) for pat in FORBIDDEN):
            bad.append(f"forbidden path tracked: {f}")
            continue
        p = REPO / f
        if p.exists() and p.stat().st_size > MAX_BYTES and f not in ALLOW_LARGE:
            bad.append(f"file over {MAX_BYTES // 1024 // 1024} MB tracked: {f} ({p.stat().st_size // 1024} KB)")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    files = tracked()
    bad = problems(files)
    if a.check:
        for b in bad:
            print("FAIL", b)
        print(f"{len(files)} tracked files, {len(bad)} problem(s)")
        return 1 if bad else 0
    files = [f for f in files if f not in {b.split(': ')[1].split(' ')[0] for b in bad}
             and not any(fnmatch.fnmatch(f, pat) for pat in EXCLUDED_FROM_ARCHIVE)]
    dist = REPO / "dist"
    dist.mkdir(exist_ok=True)
    zpath = dist / f"sentinel-forge-{__version__}-src.zip"
    manifest = {}
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            data = (REPO / f).read_bytes()
            manifest[f] = hashlib.sha256(data).hexdigest()
            info = zipfile.ZipInfo(f"sentinel-forge-{__version__}/{f}", date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    (dist / f"sentinel-forge-{__version__}-src.manifest.json").write_text(json.dumps({"version": __version__, "files": manifest}, indent=1), encoding="utf-8")
    print(f"{zpath.name}: {len(files)} files, {zpath.stat().st_size // 1024} KB; excluded {len(bad)} problem file(s)")
    for b in bad:
        print("  excluded:", b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
