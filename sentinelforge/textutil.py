"""Text helpers that never change character offsets.

Every function here returns spans in terms of the ORIGINAL report string, because evidence is only
useful if a reader can click it and land on the exact characters in the document they pasted.
`plain()` therefore substitutes characters one-for-one (a space for markdown punctuation, an ASCII
hyphen for a non-breaking one) instead of deleting them.
"""
from __future__ import annotations

import re

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}

_ONES_RE = "|".join(sorted(_ONES, key=len, reverse=True))
_TENS_RE = "|".join(_TENS)
# digits (with optional decimal) | "twenty-five" | "ninety" | "seven" ...
NUMBER = rf"(?:\d+(?:\.\d+)?|(?:{_TENS_RE})(?:[-\s](?:one|two|three|four|five|six|seven|eight|nine))?|{_ONES_RE})"

_HYPHENS = "‐‑‒–—―−"
_SPACES = "     "
_QUOTES = "“”‘’«»"
_MARKUP = "*`\"'_~|"

_PLAIN_TABLE = {}
for _c in _HYPHENS:
    _PLAIN_TABLE[ord(_c)] = "-"
for _c in _SPACES:
    _PLAIN_TABLE[ord(_c)] = " "
for _c in _QUOTES + "*`\"~|":
    _PLAIN_TABLE[ord(_c)] = " "


def plain(text: str) -> str:
    """Lower-cased, markup-free view of `text` with IDENTICAL length (asserted)."""
    out = text.translate(_PLAIN_TABLE).lower()
    if len(out) != len(text):  # a handful of Unicode chars change length under lower(); fall back per character
        out = "".join(c.translate(_PLAIN_TABLE).lower() if len(c.lower()) == 1 else c for c in text)
    assert len(out) == len(text)
    return out


def parse_number(token: str) -> int | float | None:
    """`5`, `1.5`, `five`, `twenty-five`, `ninety` -> number; anything else -> None."""
    t = token.strip().lower().replace("‑", "-")
    if not t:
        return None
    if re.fullmatch(r"\d+", t):
        return int(t)
    if re.fullmatch(r"\d+\.\d+", t):
        return float(t)
    if t in _ONES:
        return _ONES[t]
    parts = re.split(r"[-\s]+", t)
    if parts[0] in _TENS and len(parts) == 1:
        return _TENS[parts[0]]
    if parts[0] in _TENS and len(parts) == 2 and parts[1] in _ONES and 1 <= _ONES[parts[1]] <= 9:
        return _TENS[parts[0]] + _ONES[parts[1]]
    return None


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]*]*\s+(?=[\"'(\[*A-Z0-9-])|\n+")


def sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) character spans of sentence-like segments. Newlines always break a segment, since
    reports are bullet lists, tickets and chat messages as often as prose."""
    spans, cursor = [], 0
    for m in _SENTENCE_END.finditer(text):
        end = m.start() + (1 if text[m.start()] in ".!?" else 0)
        # include closing quotes/brackets that trail the terminator
        while end < len(text) and text[end] in "\"')]*" and end < m.end():
            end += 1
        if text[cursor:end].strip():
            spans.append((cursor, end))
        cursor = m.end()
    if text[cursor:].strip():
        spans.append((cursor, len(text)))
    return [_trim(text, s, e) for s, e in spans]


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def sentence_of(spans: list[tuple[int, int]], pos: int) -> tuple[int, int]:
    for s, e in spans:
        if s <= pos < e:
            return s, e
    return (pos, pos)
