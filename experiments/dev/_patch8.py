from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def edit(path, pairs):
    p = REPO / path
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert old in s, f"MISSING in {path}: {old[:80]}"
        s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")


edit("sentinelforge/reconcile.py", [
    ("def _select_count(rep: ConditionReport, beh: B.Behaviour, reasons: list[Reason]) -> CountCandidate | None:",
     r'''_REVISION = re.compile(r"\b(?:actual(?:ly)?|agreed|on further review|instead|revised|updated|corrected?|correction|supersed\w+|"
                       r"should (?:be|have been)|rather than|draft value|was a draft|no longer|not \d+)\b", re.IGNORECASE)


def _revisions(text: str, candidates, chosen_value) -> list:
    """Numbers stated in a sentence that revises / corrects something ("the actual threshold is 10, not 5"), whether or not
    that sentence looks like a rule. A correction that disagrees with the value we would compile is an unresolved conflict."""
    out = []
    for c in candidates:
        if c.value != chosen_value and _REVISION.search(text[c.evidence.sentenceStart:c.evidence.sentenceEnd]):
            out.append(c)
    return out


def _select_count(rep: ConditionReport, beh: B.Behaviour, reasons: list[Reason], text: str = "") -> CountCandidate | None:'''),
    ('''    # an unresolved candidate with a different value is a second, unexplained number: a person should look
    stray = [c for c in unresolved if c.value not in values]''',
     '''    corrections = _revisions(text, [c for c in rep.counts if c.comparator in ("unspecified", "gte") and not c.unknownObject and c.semantics in (None, beh.count_semantics)], ok[0].value)
    if corrections:
        reasons.append(Reason("COUNT_REVISED_IN_TEXT", "review",
                              "The passage revises or corrects a number (\\"" + corrections[0].evidence.quote + "\\" in a sentence that "
                              "corrects something); it cannot be assumed which value is current.", [ok[0].evidence, corrections[0].evidence]))
    # an unresolved candidate with a different value is a second, unexplained number: a person should look
    stray = [c for c in unresolved if c.value not in values]'''),
    ("def _select_window(rep: ConditionReport, reasons: list[Reason], claimed: dict | None) -> tuple[WindowCandidate | None, bool]:",
     "def _select_window(rep: ConditionReport, reasons: list[Reason], claimed: dict | None, text: str = \"\") -> tuple[WindowCandidate | None, bool]:"),
    ('''        chosen = rule[0].seconds
        alt = [w for w in rep.windows if w.ruleSentence and not w.approximate and not w.cue and w.seconds != chosen]''',
     '''        chosen = rule[0].seconds
        revised = [w for w in rep.windows if not w.approximate and w.seconds != chosen and _REVISION.search(text[w.evidence.sentenceStart:w.evidence.sentenceEnd])]
        if revised:
            reasons.append(Reason("WINDOW_REVISED_IN_TEXT", "review",
                                  "The passage revises or corrects a duration (\\"" + revised[0].evidence.quote + "\\"); it cannot be assumed which is current.",
                                  [rule[0].evidence, revised[0].evidence]))
        alt = [w for w in rep.windows if w.ruleSentence and not w.approximate and not w.cue and w.seconds != chosen]'''),
    ("        count = _select_count(rep, beh, reasons)", "        count = _select_count(rep, beh, reasons, report_text)"),
    ("        window, outside_rule_sentence = _select_window(rep, reasons, proposal.get(\"timeWindow\"))",
     "        window, outside_rule_sentence = _select_window(rep, reasons, proposal.get(\"timeWindow\"), report_text)"),
    ("from dataclasses import dataclass, field\nfrom typing import Any", "import re\nfrom dataclasses import dataclass, field\nfrom typing import Any"),
])
print("ok")
