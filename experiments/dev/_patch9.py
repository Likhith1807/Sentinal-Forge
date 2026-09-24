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
    # 1. negated / abandoned intent
    (r'''    r"\b(?:want|need|wish|expect|require|alert|fire|trigger|flag|page|escalate)\b|\bexplicitly\s+not\b|\bno\s+alert\b")''',
     r'''    r"\b(?:want|need|wish|expect|require|alert|fire|trigger|flag|page|escalate)\b|\bexplicitly\s+not\b|\bno\s+alert\b|"
    r"\bno\s+need\s+(?:to|for)\b|\b(?:decided|agreed|chose|opted)\s+(?:not\s+to|against)\b|\b(?:not\s+going\s+to|won't|will\s+not)\s+(?:build|implement|alert|add|ship)\b|"
    r"\b(?:dropped|shelved|descoped|out\s+of\s+scope)\b")'''),
    # 2. qualifiers phrased as scope limits
    (r'''    ("account-privilege restriction", r"privileged accounts? only|''',
     r'''    ("scope limited to a subset of accounts or hosts", r"\b(?:limited|restricted|scoped|confined) to (?:accounts?|users?|hosts?|machines?|servers?|systems?|the\b)|\bapplies only\b|\bonly (?:applies|apply)\b|\bin the [\w-]+ (?:group|team|department|ou)\b"),
    ("account-privilege restriction", r"privileged accounts? only|'''),
])

# hedges: a value the author says is not settled
edit("sentinelforge/conditions.py", [
    ("    methodLiterals: list = field(default_factory=list)     # concrete auth methods named inside rule sentences\n",
     "    methodLiterals: list = field(default_factory=list)     # concrete auth methods named inside rule sentences\n"
     "    hedges: list = field(default_factory=list)             # the author says a value is not settled (\"possibly 15\", \"tbc\")\n"),
    ('            "caveats": [asdict(c) for c in self.caveats],',
     '            "caveats": [asdict(c) for c in self.caveats],\n            "hedges": [asdict(h) for h in self.hedges],'),
    ("        ruleSentences=rule_sents, caveats=_caveats(text, low, sents))",
     "        ruleSentences=rule_sents, caveats=_caveats(text, low, sents), hedges=_find_hedges(text, low, sents, rule_sents))"),
    ("def _find_qualifiers(text: str, low: str, sents, rule_sents) -> list[Qualifier]:\n    out = []\n    for ss, se in rule_sents:",
     '''_HEDGE = re.compile(r"\b(?:possibly|perhaps|maybe|tentatively|tbc|tbd|to be (?:confirmed|decided|determined)|not (?:yet )?(?:sure|settled|decided|final)|"
                    r"(?:i|we)(?:'ll| will| need to| still need to| have to) (?:confirm|check|verify|decide)|still (?:to (?:confirm|decide)|open|unclear)|"
                    r"placeholder|draft value|might (?:be|need))\b")
# Exclusions are usually written as their own sentence or list item ("Exclude hosts in the scanner allow-list."), which has no
# rule cue of its own, so they are looked for across the whole passage.
_SCOPE_EXCLUSION = re.compile(r"\bexclud\w+\s+(?:all\s+|any\s+|the\s+)?(?:hosts?|accounts?|users?|ips?|systems?|machines?|servers?|service accounts?|subnets?)\b|"
                              r"\b(?:allow|deny|block)-?list|\bwhitelist|\bignore\s+(?:all\s+)?(?:hosts?|accounts?|users?|service accounts?)\b")


def _find_hedges(text: str, low: str, sents, rule_sents) -> list[Qualifier]:
    return [Qualifier("a value the author says is not settled", _evidence(text, sents, ss + m.start(), ss + m.end()))
            for ss, se in rule_sents for m in _HEDGE.finditer(low[ss:se])]


def _find_qualifiers(text: str, low: str, sents, rule_sents) -> list[Qualifier]:
    out = []
    if rule_sents:
        for m in _SCOPE_EXCLUSION.finditer(low):
            out.append(Qualifier("exclusion clause", _evidence(text, sents, m.start(), m.end())))
    for ss, se in rule_sents:'''),
])

edit("sentinelforge/reconcile.py", [
    ("    if claimed is None and proposal.get(\"provided\"):\n        # the extractor abstained",
     '''    for h in rep.hedges:
        reasons.append(Reason("VALUE_NOT_SETTLED", "review",
                              f"The passage says a value is not settled ({h.kind}: \\"{h.evidence.quote}\\"); a person must confirm it before a rule is compiled.",
                              [h.evidence]))
    if claimed is None and proposal.get("provided"):
        # the extractor abstained'''),
])
print("ok")
