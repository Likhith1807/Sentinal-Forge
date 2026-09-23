> **See [`docs/spec/independent-review-corrections.md`](../docs/spec/independent-review-corrections.md)**
> — an independent review found the "simulate a field becoming
> unavailable" control below did the opposite of what it claimed. Fixed;
> see that file for the reproduction and the fix.

# Phase 6 — Analyst Review Dashboard

A real, running web app — FastAPI backend (`backend/main.py`) + vanilla
HTML/CSS/JS frontend (`frontend/`), no build step. Wires together
components already built and verified in earlier phases rather than
reimplementing anything: `nlp/src` (extraction + injection guard),
`compiler/src` (Stage 3 validation), and the real, already-computed replay
results from `experiments/results/`.

## Run it

```
pip install -r requirements.txt
python -m uvicorn dashboard.backend.main:app --port 8000   # from repo root
```
Then open `http://127.0.0.1:8000`.

## Design choice, stated plainly

The dashboard does **not** re-run the Scala/Spark compiler (Stage 4)
synchronously per request — a cold JVM/Spark startup is 10-20+ seconds,
a bad fit for an interactive HTTP request. Extraction and Stage 3
validation (pure Python, no JVM) run live; the "real replay results" panel
serves the actual, already-verified output from Phase 4/5's real Spark
runs (`experiments/results/*.json`) rather than faking a live re-run.
That boundary is documented here, not hidden.

## The demo flow it implements

Matches the scripted flow from the project proposal: pick a report →
see the extracted spec + evidence → see Stage 3's verdict → uncheck a
field to watch the verdict degrade live → see the real replay results for
that behaviour → record an analyst decision (approve / refine) → see it
land in the audit log.

## Prompt-injection defense (`nlp/src/injection_guard.py`)

The dashboard is the first place in this project where report text
actually reaches the transformer extractor through a UI a person clicks
through, so it's also where the injection-guard defense (built this phase)
gets exercised for real. Selecting the transformer extractor on a flagged
report blocks the LLM call before it happens and requires an explicit
"Override and run LLM extractor anyway" click — classical extraction is
unaffected regardless, since it never calls a model at all. Tested against
3 real adversarial fixtures in `compiler/test/fixtures/adversarial/`
(`nlp/test_injection_guard.py`).

An unscripted result from live testing: even with the guard deliberately
overridden, the real LLM call still produced a clean, legitimate
extraction rather than being hijacked by the embedded injection text — the
constrained-vocabulary prompt (see `nlp/src/transformer_extractor.py`) is
a second layer of defense independent of the guard, and it held.

## Confidence (added to close a real Phase 6 gap; corrected 2026-09-23)

The evidence/limitations/approve-refine flow existed from the first pass,
but nothing showed a confidence signal — because nothing in the project
computed one. Rather than inventing a new score for the UI, this reuses a
real, free, already-available signal: cross-extractor agreement between
classical and the live prompted extractor's field sets. When analyzing
with the transformer extractor, classical also runs automatically as a
free second opinion (pure regex, no network call), and full disagreement
surfaces as "extractors disagree — recommend review."

**Correction, found while verifying this dashboard's Docker deployability
(not by a code review):** this section originally claimed Phase 5's n=5
calibration test (Pearson r=0.919) validated this signal as a real
confidence proxy. A rewrite of that analysis at n=44 — the corpus's real
test split — found the claim does **not** hold for classical vs. the
fine-tuned model: agreement collapses to near-zero regardless of
correctness, because classical is simply too weak an extractor overall
(Phase C: field F1 0.051) to be a meaningful second opinion, independent
of which system it's paired against. This dashboard specifically pairs
classical with the *prompted* extractor, not the fine-tuned one, and that
exact pairing hasn't been re-tested at n=44 — doing so would cost 44 more
live Groq calls against a quota already documented as fragile
(`docs/phase-c-extraction.md`), a real cost/time tradeoff left for the
project owner to decide on, not spent here without asking. The panel is
now labelled and documented as a live diagnostic, not a calibrated
confidence score — see `experiments/results/README.md`'s calibration
section and `dashboard/backend/main.py`'s `_jaccard` docstring for the
full account.

## Audit log version diffs (added to close a real Phase 6 gap)

Each decision now carries the spec it was made on, and the backend
computes a real diff against the previous decision for the same report —
fields added/removed, threshold changed, time window changed, behaviourId
changed. Visible directly in the audit log entry, not a separate view.
The first decision on a report naturally has no diff (nothing to compare
against); every subsequent one does.

## Bugs found and fixed while testing this in a real browser

Per this project's standing practice, these are documented rather than
quietly fixed and forgotten — they're genuine defects the actual browser
testing (not just reading the code) caught:

1. **Double HTML-escaping.** The report text was escaped, then inserted as
   a DOM text node — which escapes again — so `&` rendered literally as
   `&amp;`. Text nodes never need manual escaping; fixed by not
   pre-escaping.
2. **Extractor-selection desync.** Chrome's form-state restoration can
   visually re-select a `<select>` option after a page reload without
   firing a `change` event, leaving a separately-tracked JS variable stale
   while the UI showed something else — silently submitting the wrong
   extractor. Fixed by reading the `<select>`'s live DOM value at submit
   time instead of trusting the tracked variable.
3. **Misleading override message.** When classical extraction "wasn't
   blocked," the UI always credited it to an analyst override — even when
   classical was never blockable in the first place (no LLM call to
   override). Fixed to distinguish "structurally immune" from "explicitly
   overridden."
4. **Override state not persisted.** After overriding an injection block,
   toggling a field-removal checkbox silently reset the override and
   re-blocked the report. Fixed by tracking override state per report
   rather than per button click.
