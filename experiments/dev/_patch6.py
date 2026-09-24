from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def edit(path, pairs):
    p = REPO / path
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert old in s, f"MISSING in {path}: {old[:80]}"
        s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")


edit("sentinelforge/conditions.py", [
    # the sign sits BEFORE the number: slice must exclude the number's first digit
    ('negative = bool(_NEGATIVE_BEFORE.search(low[max(0, m.start("n") - 2):m.start("n") + 1]))',
     'negative = bool(_NEGATIVE_BEFORE.search(low[max(0, m.start("n") - 2):m.start("n")]))'),
    ('_NEGATIVE_BEFORE.search(low[max(0, s - 2):s + 1])', '_NEGATIVE_BEFORE.search(low[max(0, s - 2):s])'),
    # "for one account" / "against a single host" describe the grouping, not the counted object
    (r'''_GROUPING_CLAUSE = re.compile(r"\(?\s*\b(?:per|by|for\s+each|for\s+every|grouped\s+by|group\s+by)\s+[\w_]+\s*\)?")''',
     r'''_GROUPING_CLAUSE = re.compile(
    r"\(?\s*\b(?:per|by|for\s+each|for\s+every|grouped\s+by|group\s+by)\s+[\w_]+\s*\)?|"
    r"\b(?:for|against|on|from)\s+(?:the\s+same|one|a\s+single|a|an|each|every|any|that|this)\s+(?:source\s+|originating\s+)?[\w_]+")'''),
])

edit("sentinelforge/reconcile.py", [
    ('''"sliding", "hyphenated", "in", "over", "during"}''', '''"sliding", "hyphenated", "in", "over", "during", "under", "label", "abbreviated"}'''),
])

edit("sentinelforge/textutil.py", [
    ("    return [_trim(text, s, e) for s, e in spans]\n",
     r'''    spans = [_trim(text, s, e) for s, e in spans]
    return _merge_structured(text, spans)


_TABLE_ROW = re.compile(r"^\s*\|")
_KV_LINE = re.compile(r"^\s*[-*]?\s*[A-Za-z][A-Za-z _\-]{0,28}:\s*\S")


def _merge_structured(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """A markdown table or a block of `key: value` lines states ONE thing across several lines ("count: 5 or more /
    window: 90 seconds / then: successful login"); treating each row as its own sentence separates the pieces of one rule."""
    out: list[tuple[int, int]] = []
    for s, e in spans:
        seg = text[s:e]
        if out:
            ps, pe = out[-1]
            gap = text[pe:s]
            if gap.strip() == "" and gap.count("\n") == 1:
                prev = text[ps:pe].split("\n")[-1]
                if (_TABLE_ROW.match(seg) and _TABLE_ROW.match(prev)) or (_KV_LINE.match(seg) and _KV_LINE.match(prev)):
                    out[-1] = (ps, e)
                    continue
        out.append((s, e))
    return out
'''),
])

edit("tests/core/test_phrasing_classes.py", [
    ("and a good login follows straight after.", "and a successful login follows straight after."),
])
print("ok")
