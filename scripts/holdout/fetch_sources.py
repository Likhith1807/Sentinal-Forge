"""Fetch the public advisories the real-passage entries are excerpted from (needed only to REBUILD the holdout;
the excerpts themselves are committed under data/holdout/reports/).

    python scripts/holdout/fetch_sources.py
"""
from __future__ import annotations

import html
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "data" / "holdout" / "_src"
PAGES = {"aa21-116a": "https://www.cisa.gov/news-events/cybersecurity-advisories/aa21-116a",
         "aa22-074a": "https://www.cisa.gov/news-events/cybersecurity-advisories/aa22-074a"}


def to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer).*?</\1>", " ", raw)
    raw = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", raw))
    raw = re.sub(r"[ \t]+", " ", raw)
    return "\n".join(l.strip() for l in raw.split("\n") if len(l.strip()) > 40)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, url in PAGES.items():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        text = to_text(urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace"))
        (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
        print(name, len(text))


if __name__ == "__main__":
    main()
