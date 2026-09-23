# Second-annotator review of the holdout

**Status of the human review: NOT DONE.** Read this before quoting any number from the holdout.

| Reviewer | Type | Coverage | Result |
|---|---|---|---|
| Author | human (single annotator) | all 112 | the gold labels |
| `qwen/qwen3.8-27b` | LLM, blind, different vendor from the prompted baseline | all 112 | label agreement 94.6 %, Cohen's kappa 0.886 - [`llm_review_result.json`](llm_review_result.json) |
| Independent human | - | **pending** | send [`human_review_sheet.csv`](human_review_sheet.csv) (blind: no gold, no hints) |

What happened, in order:

1. The author wrote every report and gold label.
2. The LLM reviewer labelled all 112 reports blind, following only the written guide
   (the guide is `GUIDE` in `scripts/holdout/llm_review.py`; every attempt and retry is in
   [`llm_review_attempts.jsonl`](llm_review_attempts.jsonl)).
3. Six disagreements were adjudicated by the author with a written reason each
   ([`adjudication.json`](adjudication.json)): three gold labels were **wrong and were changed**, three
   exposed gaps in the guide, which were clarified (whole-second windows up to 7 days, English only).
4. The holdout was then frozen (`FROZEN.json`, git tag `holdout-v1-frozen`).

Limits, stated plainly: an LLM reviewer is not a substitute for a human, it shares blind spots with other LLMs,
and the adjudicator is the same person who wrote the labels. Agreement between the author and one model says the
labels are *not obviously inconsistent*; it does not establish that they are correct. To close this gap, a
practitioner should fill in the sheet, run `python scripts/holdout/score_human_review.py <sheet.csv>` and commit
the result as `RESULT.json`; any disagreement found then should be reported alongside the results, not used to
quietly edit the gold.
