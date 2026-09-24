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
    (r'''(?:(?:reach|meet)(?:es|ed|ing)?\s+or\s+exceed''', r'''(?:(?:reach|meet)(?:s|es|ed|ing)?\s+or\s+exceed'''),
    (r'''for|on|at|by|that|which|where|without|of|to|is|are|was|were|has|have|had)\b|[,;:.()|]")''',
     r'''for|on|at|by|that|which|where|without|of|to|is|are|was|were|has|have|had)\b|[,;:.()|\n]")'''),
    ('''        semantics, kind, unknown = _classify_subject(label, low[max(0, s - 120):s], "", low[max(0, s - 200):s], "")''',
     '''        ss, se = sentence_of(sents, s)
        semantics, kind, unknown = _classify_subject(label, low[ss:s], low[e:se], low[max(0, s - 200):s], low[e:e + 200])'''),
])

edit("tests/core/test_phrasing_classes.py", [
    ('''def test_negative_count_is_rejected_not_reviewed():
    a = analyze("Alert when -5 or more failed logins hit one account within 3 minutes and then it succeeds.", None)
    assert a.status == "rejected" and "COUNT_INVALID_NUMBER" in a.reconciliation.codes''',
     '''def test_negative_count_is_rejected_not_reviewed():
    claim = {"behaviourId": B1, "threshold": {"failureCount": 5}, "timeWindow": {"amount": 3, "unit": "minutes"}}
    a = analyze("Alert when -5 or more failed logins hit one account within 3 minutes and then it succeeds.", claim)
    assert a.status == "rejected" and "COUNT_INVALID_NUMBER" in a.reconciliation.codes'''),
])
print("ok")
