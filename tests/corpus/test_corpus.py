"""Tests for the report corpus (scripts/corpus). Offline: the LLM is replaced by a fake client.

Run directly (``python tests/corpus/test_corpus.py``) or collect with pytest.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "compiler" / "src"))

import observability_checker  # noqa: E402
import spec_bridge  # noqa: E402

from scripts.corpus import agreement, build, facts, llm_rewrite, render, split  # noqa: E402
from scripts.corpus.unsupported import TOPICS, sample_unsupported  # noqa: E402
from scripts.corpus.vocab import ALL_FIELDS, B1, B2, B3, B4, B5, BEHAVIOUR_IDS  # noqa: E402

UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600}


def _all_facts(n_per=25):
    for b in BEHAVIOUR_IDS:
        for i in range(1, n_per + 1):
            yield facts.sample(b, i, random.Random(f"t|{b}|{i}"))
    for i in range(1, len(TOPICS) + 1):
        yield sample_unsupported(i, random.Random(f"t|u|{i}"))


# ----------------------------------------------------------------------------- rendering + gold
def test_every_family_and_style_renders_with_verified_spans():
    n = 0
    for f in _all_facts():
        for style in render.STYLES:
            text, spans, _ = render.render(f, style, random.Random(1), "X-1")
            for s in spans:
                assert text[s["start"]:s["end"]] == s["text"]
            if f.threshold is not None:
                assert render.thr_expression(f) in text
                assert render.win_expression(f) in text
            n += 1
    assert n == (5 * 25 + len(TOPICS)) * len(render.STYLES)


def test_gold_fields_match_the_tagged_mentions_in_the_text():
    for f in _all_facts(10):
        text, spans, structure = render.render(f, "advisory", random.Random(2), "X-1")
        gold = render.gold_record(f, "X-1", "x.md", "advisory", "template", spans, structure)
        mentioned = {s["field"] for s in spans if s["label"] == "required_field"}
        if f.supported:
            assert mentioned == set(gold["requiredFields"]) | set(gold["policyFields"]), f.family_id
            assert set(gold["excludedFields"]).isdisjoint(mentioned)
            assert {s["field"] for s in spans if s["label"] == "excluded_field"} == set(gold["excludedFields"])
        else:
            assert gold["behaviourId"] is None and gold["supported"] is False
            assert set(gold["unavailableFields"]).isdisjoint(ALL_FIELDS), "unavailable fields must be outside the schema"


def test_more_than_form_shows_threshold_minus_one_but_gold_is_the_threshold():
    seen = 0
    for f in _all_facts(60):
        if f.threshold_form != "more-than":
            continue
        text, spans, _ = render.render(f, "soc-ticket", random.Random(3), "X-1")
        span = next(s for s in spans if s["label"] == "threshold")
        assert span["value"] == f.threshold and span["form"] == "more-than"
        shown = span["text"]
        assert shown in (str(f.threshold - 1), render.NUMBER_WORDS.get(f.threshold - 1, "?"))
        assert f"more than {shown}" in text
        seen += 1
    assert seen > 5


def test_unsupported_reports_do_not_announce_they_are_unsupported():
    for i in range(1, len(TOPICS) + 1):
        f = sample_unsupported(i, random.Random(i))
        for style in render.STYLES:
            text, _, _ = render.render(f, style, random.Random(i), "X-1")
            assert not llm_rewrite.LEAK_PATTERN.search(text), (f.entities["topic"], style)


def test_corpus_covers_the_diversity_axes():
    forms, modes, wforms, numm = set(), set(), set(), set()
    for f in _all_facts(60):
        if f.threshold is not None:
            forms.add(f.threshold_form); wforms.add(f.window_form)
        modes.add(f.field_mode); numm.add(f.num_mode)
    assert forms == set(render.FORMS) and wforms == {"spaced", "hyphen"}
    assert modes == {"code", "natural"} and numm == {"digits", "words"}


# ----------------------------------------------------------------------------- gold flows through the pipeline
def test_supported_gold_passes_stage3_and_compiles_to_the_gold_numbers():
    checked = 0
    for f in _all_facts(25):
        _, spans, structure = render.render(f, "advisory", random.Random(4), "X-1")
        gold = render.gold_record(f, "X-1", "x.md", "advisory", "template", spans, structure)
        spec = {k: gold[k] for k in ("behaviourId", "requiredFields", "policyFields", "threshold", "timeWindow")}
        if f.supported:
            assert observability_checker.validate(spec).status == "supported", (f.family_id, spec)
            compiled = spec_bridge.build_compiled_spec(spec)
            if gold["threshold"]:
                value = next(iter(gold["threshold"].values()))
                assert value in (compiled["countThreshold"], compiled["distinctThreshold"])
                w = gold["timeWindow"]
                assert compiled["timeWindowSeconds"] == w["amount"] * UNIT_SECONDS[w["unit"]]
            checked += 1
        else:
            # the honest downstream outcome for an out-of-scope report: nothing valid to validate
            rejected = observability_checker.validate(
                {"behaviourId": "unrecognized:" + f.entities["topic"], "requiredFields": gold["unavailableFields"],
                 "policyFields": [], "threshold": None, "timeWindow": None})
            assert rejected.status == "rejected"
    assert checked == 5 * 25


# ----------------------------------------------------------------------------- split + leakage
def test_split_is_by_family_stratified_and_deterministic():
    groups = {b: [f"F-{i}-{b}" for i in range(20)] for b in BEHAVIOUR_IDS}
    groups["unsupported"] = [f"F-U-{i}" for i in range(10)]
    a, b, c = split.family_split(groups, 42), split.family_split(groups, 42), split.family_split(groups, 43)
    assert a == b and a != c
    for stratum, fams in groups.items():
        counts = {s: sum(a[f] == s for f in fams) for s in split.SPLITS}
        assert counts == ({"train": 12, "dev": 4, "test": 4} if stratum != "unsupported"
                          else {"train": 6, "dev": 2, "test": 2}), (stratum, counts)


def test_leakage_gate_can_fail_and_does_flag_a_near_duplicate_across_splits():
    text = ("the account failed to sign in repeatedly from the gateway and then succeeded so the detection should "
            "fire when the same account fails several times inside a short window")
    texts = {"a": text, "b": text.replace("gateway", "portal"), "c": "completely unrelated words " * 12}
    fam = {"a": "F1", "b": "F2", "c": "F3"}
    spl = {"a": "train", "b": "test", "c": "test"}
    beh = {"a": "x", "b": "x", "c": "y"}
    tier = {r: "template" for r in texts}
    leaky = split.leakage_report(texts, spl, fam, tier, beh, max_cross_raw=0.5)
    assert leaky["gate"]["passed"] is False and leaky["mostSimilarCrossSplitPairs"][0]["rawJaccard"] > 0.5
    ok = split.leakage_report(texts, {"a": "train", "b": "train", "c": "test"}, fam, tier, beh, max_cross_raw=0.5)
    assert ok["gate"]["passed"] is True


def test_a_family_never_straddles_splits_in_a_real_build():
    with tempfile.TemporaryDirectory() as tmp:
        m = _build(Path(tmp) / "c", skip_llm=True)
        assert m["leakageGatePassed"]
        leak = json.loads((Path(tmp) / "c" / "leakage.json").read_text())
        assert leak["familiesStraddlingSplits"] == [] and leak["exactDuplicateTexts"] == 0


# ----------------------------------------------------------------------------- LLM rewrite guards
def _sample_report():
    f = facts.sample(B1, 3, random.Random(11))
    text, spans, _ = render.render(f, "soc-ticket", random.Random(5), "X-1")
    return f, text, spans


def _check(f, text, spans, candidate, supported=True):
    phrases = llm_rewrite.keep_phrases(spans, render.thr_expression(f), render.win_expression(f))
    return llm_rewrite.validate_rewrite(text, candidate, spans, phrases, llm_rewrite._identifiers(f.entities, f.meta),
                                        render.thr_expression(f), render.win_expression(f), supported)


def test_rewrite_guard_accepts_a_faithful_rewrite_and_relocates_every_span():
    f, text, spans = _sample_report()
    relocated, reason = _check(f, text, spans, "Reworded for the review board. " + text)
    assert relocated is not None, reason
    candidate = "Reworded for the review board. " + text
    for s in relocated:
        assert candidate[s["start"]:s["end"]] == s["text"]
    assert {s["label"] for s in relocated} >= {"threshold", "window_amount", "window_unit", "required_field"}


def test_rewrite_guard_rejects_each_kind_of_drift():
    f, text, spans = _sample_report()
    thr, win = render.thr_expression(f), render.win_expression(f)
    cases = {
        "dropped threshold phrase": text.replace(thr, "a handful of"),
        "dropped window phrase": text.replace(win, "a short time"),
        "invented number": text + " An additional 987 events were seen.",
        "lost identifier": text.replace(f.entities["acct"], "the user"),
        "too short": "Too short.",
    }
    for name, candidate in cases.items():
        assert _check(f, text, spans, candidate)[0] is None, name
    u = sample_unsupported(4, random.Random(4))
    ut, us, _ = render.render(u, "soc-ticket", random.Random(1), "X-1")
    leak = ut + " This is unsupported by the schema."
    phrases = llm_rewrite.keep_phrases(us, None, None)
    assert llm_rewrite.validate_rewrite(ut, leak, us, phrases, [], None, None, False)[0] is None


def test_rewrite_guard_tolerates_a_dropped_ticket_and_a_dropped_incidental_note_but_not_a_dropped_required_field():
    # a family whose report has an excluded (incidental) field mention
    f = next(x for x in (facts.sample(B1, i, random.Random(i)) for i in range(1, 60))
             if x.excluded_fields and x.field_mode == "natural")
    text, spans, _ = render.render(f, "soc-ticket", random.Random(5), "X-1")
    excluded = next(s for s in spans if s["label"] == "excluded_field")["text"]
    required = next(s for s in spans if s["label"] == "required_field")["text"]

    no_ticket = "Reworded. " + text.replace(f.meta["ticket"], "the ticket").replace(f.meta["analyst"], "an analyst")
    assert _check(f, text, spans, no_ticket)[0] is not None, "ticket/analyst are distractors, not gold"

    no_note = "Reworded. " + text.replace(excluded, "another attribute")
    relocated, reason = _check(f, text, spans, no_note)
    assert relocated is not None, reason
    assert not any(s["label"] == "excluded_field" for s in relocated), "the dropped note must not stay in the gold"

    no_required = "Reworded. " + text.replace(required, "some data")
    assert _check(f, text, spans, no_required)[0] is None, "a required-field phrase must survive"


def test_numbered_list_markers_are_not_invented_numbers():
    f, text, spans = _sample_report()
    listed = "1. " + text.replace("\n\n", "\n2. ", 1)
    assert _check(f, text, spans, listed)[0] is not None


class _FakeClient:
    """chat.completions.create(...) -> object with .choices[0].message.content."""

    def __init__(self, mode="faithful"):
        self.mode, self.calls = mode, 0
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.calls += 1
                prompt = kw["messages"][0]["content"]
                original = prompt.split("Original:\n---\n", 1)[1].rsplit("\n---", 1)[0]
                if outer.mode == "error":
                    raise RuntimeError("boom")
                content = original if outer.mode == "bad-first" and outer.calls == 1 else "Reworded. " + original
                if outer.mode == "bad-first" and outer.calls == 1:
                    content = "nope"
                msg = type("M", (), {"content": content})()
                return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

        self.chat = type("Chat", (), {"completions": _Completions()})()


def test_rewrite_retries_after_a_rejected_attempt_and_reports_api_errors():
    f, text, spans = _sample_report()
    kw = dict(text=text, spans=spans, thr_expr=render.thr_expression(f), win_expr=render.win_expression(f),
              entities=f.entities, meta=f.meta, supported=True, style="email", rng=random.Random(1), model="fake")
    r = llm_rewrite.rewrite(_FakeClient("bad-first"), **kw)
    assert r["ok"] and r["tries"] == 2
    e = llm_rewrite.rewrite(_FakeClient("error"), **kw)
    assert not e["ok"] and e["reason"].startswith("api error")


# ----------------------------------------------------------------------------- build end to end (fake LLM)
def _build(out: Path, skip_llm: bool, model="fake"):
    args = argparse.Namespace(out=str(out), seed=42, families_per_behaviour=5, unsupported_families=4,
                              skip_llm=skip_llm, llm_workers=2, model=model, max_cross_jaccard=0.5, verification_n=6)
    return build.build(args)


def test_build_with_fake_llm_produces_two_tiers_consistent_gold_and_a_blind_sample():
    original = llm_rewrite.make_client
    llm_rewrite.make_client = lambda: _FakeClient("faithful")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "c"
            m = _build(out, skip_llm=False)
            assert m["byTier"] == {"llm-rewrite": 29, "template": 29} and m["llm"]["accepted"] == 29
            gold = agreement.load_gold(out)
            assert len(gold) == 58
            for rid, g in gold.items():
                text = (out / "reports" / f"{rid}.md").read_text(encoding="utf-8")
                assert all(text[s["start"]:s["end"]] == s["text"] for s in g["spans"]), rid
            # the LLM tier inherits its family's gold values exactly
            for rid, g in gold.items():
                if g["tier"] == "llm-rewrite":
                    base = gold[g["derivedFrom"]]
                    for key in ("behaviourId", "requiredFields", "policyFields", "threshold", "timeWindow"):
                        assert g[key] == base[key], (rid, key)
                    assert set(g["excludedFields"]) <= set(base["excludedFields"]), rid   # a dropped note may remove one
            # the annotation sheet is blind (no gold) and drawn only from the test split
            sheet = [json.loads(l) for l in (out / "verification" / "annotation_template.jsonl").read_text().splitlines()]
            splits = json.loads((out / "splits.json").read_text())["reports"]
            assert len(sheet) == 6 and all(splits[x["reportId"]] == "test" for x in sheet)
            assert all(x["annotation"]["behaviourId"] is None and x["annotation"]["supported"] is None for x in sheet)
            assert "threshold" not in json.dumps([{k: v for k, v in x.items() if k != "annotation"} for x in sheet])
            # rebuild from the cache must not call the API again and must be identical
            llm_rewrite.make_client = lambda: (_ for _ in ()).throw(AssertionError("API called on a cached rebuild"))
            m2 = _build(out, skip_llm=False)
            assert m2["llm"]["cached"] == 29 and m2["reports"] == m["reports"]
    finally:
        llm_rewrite.make_client = original


def test_offline_build_is_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        _build(a, skip_llm=True)
        _build(b, skip_llm=True)
        names = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
        assert names == sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
        for n in names:
            assert (a / n).read_bytes() == (b / n).read_bytes(), n


# ----------------------------------------------------------------------------- agreement tool
def _corpus_with_sheet(tmp):
    llm_rewrite.make_client = lambda: _FakeClient("faithful")
    out = Path(tmp) / "c"
    _build(out, skip_llm=False)
    return out


def test_agreement_scores_perfect_and_imperfect_annotation_and_refuses_blank():
    original = llm_rewrite.make_client
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = _corpus_with_sheet(tmp)
            gold = agreement.load_gold(out)
            sheet = [json.loads(l) for l in (out / "verification" / "annotation_template.jsonl").read_text().splitlines()]

            def fill(item, mutate=False):
                g = gold[item["reportId"]]
                a = {"supported": g["supported"], "behaviourId": g["behaviourId"], "requiredFields": list(g["requiredFields"]),
                     "policyFields": list(g["policyFields"]), "excludedFields": list(g["excludedFields"]),
                     "threshold": g["threshold"], "timeWindow": g["timeWindow"]}
                if mutate and g["supported"]:
                    a["behaviourId"] = B2 if g["behaviourId"] != B2 else B3
                    a["requiredFields"] = []
                return {**item, "annotation": a}

            perfect = agreement.score(gold, [fill(x) for x in sheet])
            assert perfect["labelKappa"] == 1.0 and perfect["fieldMicroF1"] == 1.0
            assert perfect["thresholdExactMatch"] == 1.0 and perfect["windowExactMatch"] == 1.0
            assert perfect["disagreements"] == []

            worse = agreement.score(gold, [fill(x, mutate=True) for x in sheet])
            assert worse["labelAgreement"] < 1.0 and worse["fieldMicroF1"] < 1.0 and worse["disagreements"]

            try:
                agreement.score(gold, sheet)
            except ValueError as exc:
                assert "blank" in str(exc)
            else:
                raise AssertionError("a blank annotation sheet must not be scoreable")

            equivalent = fill(sheet[0])
            g = gold[equivalent["reportId"]]
            if g["timeWindow"] and g["timeWindow"]["unit"] == "minutes":
                equivalent["annotation"]["timeWindow"] = {"amount": g["timeWindow"]["amount"] * 60, "unit": "seconds"}
                assert agreement.score(gold, [equivalent])["windowExactMatch"] == 1.0, "60s must equal 1 minute"
    finally:
        llm_rewrite.make_client = original


def test_cohen_kappa_basic_properties():
    assert agreement.cohen_kappa(list("aabb"), list("aabb")) == 1.0
    assert agreement.cohen_kappa(list("aabb"), list("bbaa")) < 0
    assert agreement.cohen_kappa([], []) is None


def _run_all():
    failures = 0
    names = sorted(n for n in globals() if n.startswith("test_") and callable(globals()[n]))
    for name in names:
        try:
            globals()[name]()
            print(f"OK   {name}")
        except Exception as exc:  # noqa: BLE001 - report every failure, then exit non-zero
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(names) - failures}/{len(names)} cases passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
