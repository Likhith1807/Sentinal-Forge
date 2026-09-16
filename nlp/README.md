# NLP Extraction (Phase 2)

Two extractors, scored against hand-authored gold labels on the 5 held-out
reports (`data/samples/ir/gold/`, see [`docs/data-splits.md`](../docs/data-splits.md)).
Both are constrained to the same controlled vocabulary
(`src/schema_fields.py`) so the comparison is fair — an extractor is only
scored on whether it names the *right* field/behaviour from a fixed list,
never on how it phrases something.

Run it yourself: `python nlp/src/evaluate.py`. Results also written to
`experiments/results/phase2_extraction_eval.json`.

## What each one is

- **`src/classical_extractor.py`** — regex and keyword rules over the
  report's semi-structured "Analyst-confirmed detection parameters"
  section. No learning, no model weights. This is the honest "2015-era"
  baseline.
- **`src/transformer_extractor.py`** — a pretrained Transformer (Groq,
  `openai/gpt-oss-120b`) prompted for structured, in-context extraction.
  **Not fine-tuned** — with 10 training reports, "training" a model would
  mean memorizing them, not generalizing, so nothing here claims to.
  Growing this into an actually fine-tuned model is realistic future work
  once report volume justifies it, not something claimed now.

## Results (this run)

| System | micro P | micro R | micro F1 | behaviour classification acc | provenance verification rate |
|---|---|---|---|---|---|
| Classical | 1.00 | 0.947 | **0.973** | 0.80 | 1.00 |
| Transformer | 1.00 | 0.895 | **0.944** | **1.00** | 1.00 |

Classical edges out the transformer on field-extraction F1 in this run —
not the outcome a "the AI one is always better" narrative would predict,
and that's exactly why it's worth reporting honestly rather than reaching
for a cleaner-sounding story.

### Why classical scores well on fields, but worse on behaviour ID

The "Analyst-confirmed detection parameters" section literally states
field names in backticks (`` `mfa_used` ``, `` `expected_auth_method` ``),
and every report in this project follows that template — so a rule that
reads that one section closely does well *on this dataset, by
construction*. It has no real understanding of the report; it would break
immediately on a differently-formatted report. Two concrete failures
confirm this:

1. **SF-SAMPLE-011 (B1, held out) misclassified as B3.** The narrative
   mentions "a different host" in passing (contrasting this incident's
   host with the training report's), and the classical extractor's
   keyword rule for concurrent-sessions includes "different host" —
   triggering a false match on an incidental phrase, not the actual
   behaviour.
2. **SF-SAMPLE-015 (B5) misses `mfa_used`.** The negation-detection rule
   checks a 100-character window after a field mention for phrases like
   "not part of" — but in this report, that window bleeds into the *next*
   bullet point's unrelated negation ("`auth_method` is not part of..."),
   incorrectly marking `mfa_used` as excluded too. This is a genuine,
   reproducible bug in proximity-based negation scoping, not a synthetic
   example — see `is_negated()` in `classical_extractor.py`. Left
   unfixed on purpose: it's a real, representative failure mode of naive
   rule systems, and "fixing" it just to raise the number would defeat
   the point of running this comparison at all.

### Where the transformer actually wins, and where it doesn't

The transformer correctly identified all 5 behaviours (1.00 vs 0.80) —
including SF-SAMPLE-011, where it wasn't fooled by the "different host"
phrase. It also correctly excluded `auth_method` from B1 and B5 in every
run, respecting the reports' explicit "not part of the pattern" callouts —
the same discipline the direct-LLM code-generation baseline in
`experiments/baselines/direct_llm/` notably *failed* to show when asked to
generate a rule directly instead of fill a constrained extraction schema.
That contrast (same underlying model, different task framing, different
outcome) is itself a real finding about *how* you ask an LLM to do
extraction, not just *whether* you do.

Its actual misses were on the two policy-reference fields
(`policy.expected_auth_method`, `policy.mfa_required`) on the two
behaviours that need them — and a repeat run showed those specific misses
are not fully stable even at `temperature=0`: Groq's API did not reproduce
identical output field-for-field between two runs of the same prompt. That
non-determinism is itself worth flagging rather than papering over with a
single cherry-picked run — a more robust number would average several
runs per report, which is a natural Phase 2 follow-up once this matters
for a final reported figure rather than a first real pass.

## Evidence traceability: both extractors verified, not asserted

Every field either extractor outputs carries a `provenance` entry with a
real character offset into the report — `classical_extractor.py`'s spans
come straight from the regex matches that produced the answer;
`transformer_extractor.py` asks the model to quote its evidence verbatim,
then **independently checks** that quote against the actual report text
(`transformer_extractor.py`'s verification step) rather than trusting it.

The first run of that check came back at only 84.2% verified for the
transformer, which would have been a genuinely bad result — until
inspecting the 3 failures showed the cause: these reports are hand-wrapped
markdown, so a phrase like "sign-in attempts" is stored in the file as
"sign-in\nattempts". The model quoted it correctly with a plain space; the
verifier's exact-string check was too strict, not the model's answer too
loose. Making the check whitespace-tolerant (`transformer_extractor.py`,
`re.sub(r"(?:\\ )+", r"\\s+", pattern)`) brought it to 100% — a case worth
leaving in this document rather than quietly fixing and forgetting, since
it's a real example of the kind of measurement bug that silently
undercounts a system's real quality if nobody checks *why* a metric came
in low.

## What this Phase 2 pass is, and isn't, good for

Five held-out reports produces a real, honestly-computed F1, and the
qualitative failures above are worth more than the number itself — they
directly motivate Stage 3's design (an automated check can't rely on either
extractor being right on its own; it has to verify against the actual log
schema regardless of which extractor produced the spec). It is not enough
data to claim a statistically robust F1, and this file makes no such claim.
