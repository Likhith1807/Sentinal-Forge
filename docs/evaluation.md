# Evaluation

This page says what was measured, on which data, how many times, and what it does **not** show. Every number below is
read from a committed file under `experiments/results/`; the command that regenerates it is next to it. The Evaluation screen
of the dashboard shows the same files, labelled *archived* — nothing there is computed live.

## 0. The rule that governs this page

A number is only worth quoting if it was measured on data nobody tuned against. So the data has three kinds, and each result
says which kind it is:

| kind | meaning | what it can show |
|---|---|---|
| **training / regression** | the synthetic corpus (201 reports) the parser and model were developed on, plus the 44-report split from the original audit | that a defect was fixed; **not** how good the system is |
| **frozen holdout** | authored reports, SHA-256-frozen and git-tagged *before* any system ran on them; one run; failures published | how the system behaves on wording it has not seen |
| **synthetic scale data** | generated authentication logs | that the engine is correct and how fast it is; not that it finds real attacks |

The original 44-report evaluation set has been **retired as evidence of quality**: it was used to develop the parser, so it now
appears only as a regression test.

## 1. The audit: silent failures, before and after (regression data)

`experiments/audit/pipeline_audit.py` replays the 44-report split (40 supported, 4 unsupported) through the old pipeline and the current one.

| | correct | wrong rule, silently | unsupported report silently accepted | refused although supported |
|---|---|---|---|---|
| original pipeline (`audit_pipeline_baseline.json`) | 27 / 40 | 5 | 2 | 8 |
| current, evidence-only (`audit_pipeline_fixed.json`) | 38 / 40 | **0** | **0** | 1 (+1 sent to review) |

The audit's *silent* failures — 90 seconds becoming 90 minutes, an ambiguous count read as distinct accounts, an unsupported
behaviour accepted because a classifier score was high — are now 0, and each has a named regression test in
`tests/core/test_audit_regressions.py`. One report (`SFC-0044`) went from "correct" to "refused" during the last parser
tightening, and that is the right call: its text says *the rule is scoped to the 10.19.247.0/24 subnet*, a restriction no
supported recipe can evaluate, so compiling it silently drops a condition the author wrote. The corpus label still says
"supported"; the label is arguably wrong under the stricter standard, and it has not been edited to flatter the number.

This is regression data. The model and the parser were developed against these reports.

## 2. Frozen holdout v1 (measured before the parser was generalised)

112 reports: 41 that must compile, 71 that must not (unsupported behaviours, ambiguous or contradictory numbers, qualifiers no
recipe evaluates, prompt-injection attempts, and 16 verbatim passages from CISA/FBI joint advisories). Frozen 2026-09-24 (tag
`holdout-v1-frozen`), `data/holdout/FROZEN.json` carries a hash for every file. One run. Failures are in
`experiments/results/holdout/FAILURE_ANALYSIS.md`, generated, with the report text beside each.

Labels: one annotator, plus a blind review by an LLM from a different vendor (Cohen's kappa 0.886; six disagreements adjudicated,
listed in `data/holdout/review/adjudication.json`). **No human second reviewer has looked at it.** `data/holdout/review/`
contains the sheet and the scorer for one.

| system | complete rule correct | wrong rule, **silently** | supported but not compiled | must-not-compile accepted |
|---|---|---|---|---|
| regex baseline + evidence check | 7% | 0% | 93% | 4% |
| fine-tuned model, **raw** (no evidence check) | 61% | **32%** | 7% | **56%** |
| fine-tuned model + evidence check | 32% | 0% | 68% | 6% |
| **evidence-only** (no model) | **44%** | 0% | 56% | 6% |
| prompted LLM (`gpt-oss-20b`) | *partial: 35–37% of reports scored — see below* | | | |

Three findings, none of them flattering, all of them the point:

1. **The raw model is fast and confidently wrong.** Left alone it compiles a wrong rule for 32% of supported reports and accepts
   56% of the reports that must be refused. That is the failure mode the evidence check exists for.
2. **The evidence check removes every silent error** (0%), at the price of refusing more than half of the supported reports.
   Refusal is the designed failure: an analyst is asked, nothing wrong is compiled.
3. **A model in the loop lowered recall** (32% vs 44%): the evidence check disagrees with the model more often than it rescues it. The
   deterministic finder is therefore the default extractor, and the model stays selectable.

The prompted-LLM comparison is **incomplete**. The provider's daily token quota ran out twice; the 120B model was replaced by the 20B
model, and only 35–37% of holdout reports were scored before the quota ended again. The rows carry a `coverage` field and the UI
labels them *partial*. The eight prompted-LLM reports it did score are not a comparison and are not quoted as one. Two aborted runs are archived in
`experiments/results/holdout/aborted-run-*`. To finish the row: `python experiments/holdout/run_eval.py --llm-model openai/gpt-oss-20b`
(resumable; the cache is `llm_cache.jsonl`). Every LLM call is kept in `attempts.jsonl` with its first attempt and each retry.

**Quote verification is not semantic verification.** "The cited span exists at those offsets" is a substring check and is reported as
`quoteVerificationRate`. Whether the quote *supports the extracted meaning* is judged by the condition finder and, for the ambiguous
cases, by a person; the two are reported separately.

## 3. What v1 taught, and holdout v2

The v1 failure analysis grouped failures into phrasing classes (comparator spellings, key/value and table layouts, glued windows,
"no need to alert" style negations, corrections that contradict an earlier number, …). The parser was generalised for those
*classes*, not for the sentences, and each class has a regression test in **new** wording (`tests/core/test_phrasing_classes.py`).
Because v1 is then consumed — a holdout you fix against is no longer a holdout — a **second, fresh holdout** was written:

**Frozen holdout v2** — 71 reports (30 must compile, 41 must not), new layouts (email, ticket acceptance criteria, runbook step,
Sigma-like YAML, checklist, chat), new must-not-compile classes, 11 more real CISA/FBI passages. Tag `holdout-v2-frozen`, commit
`ab8c006`. Single run, published as it came out:

| system | complete rule correct | wrong rule, silently | supported but not compiled | must-not-compile accepted |
|---|---|---|---|---|
| regex baseline + evidence check | 10% | 0% | 90% | 7% |
| fine-tuned model, raw | 67% | **23%** | 10% | **63%** |
| fine-tuned model + evidence check | 50% | 0% | 50% | 10% |
| **evidence-only (default)** | **63%** (19 of 30) | 0% | 37% | 12% (5 of 41) |

Recall went from 44% to 63% on wording written *after* the fixes; the silent-error rate stayed at zero. It is not 100%, and the
five must-not-compile reports it accepted are exactly what a reader should look at first: a hedged number ("possibly 15"), two
abandoned intents ("no need to alert…", "decided not to build it"), and two scope limits stated in a separate sentence or list item
("Exclude hosts in the allow-list."; "limited to accounts in the finance group"). Those five were fixed afterwards, each with a
regression test, and the result files were **not** re-cut: `experiments/results/holdout_v2/POST_HOC.md` records what was measured and
what was changed. Re-running the fixed parser over the v2 texts gives 0 false accepts and 19/30 — but that is *not* a held-out
measurement, because the fixes were written after seeing those failures. The next honest number needs a v3 holdout.

Limits of v2, stated plainly: only the author has labelled it (the blind LLM review v1 had was not run because of the same quota);
30 supported reports means wide intervals (see `summary.json`); the prompted-LLM rows were not run on it.

## 4. Downstream: does the extraction change what is detected?

Extraction accuracy is not the point; detection is. `run_eval.py` compiles each system's rule and runs it on the demonstration
dataset, then compares the alert set with the gold rule's alerts (`downstream` in `summary.json`). A wrong count or a lost window
shows up as missing or spurious alerts, not as a JSON diff. On v2 the evidence-only path reaches downstream precision 0.78 / recall
0.60 over all 71 reports (refusing counts as no alert); the raw fine-tuned model has recall 0.75 but precision 0.34 — many more
alerts, most of them for rules nobody asked for.

## 5. The engine, separately from the language model

| check | result | file / command |
|---|---|---|
| Spark = independent reference engine (generated scenarios, all 5 behaviours) | **750 scenarios (150 per behaviour, seed 21), 0 mismatches**; CI re-runs 40 per behaviour on every push | `experiments/results/differential_large.json`, `scripts/verify/differential.py`, `tests/integration/test_spark_agreement.py` |
| The differential harness can see defects | **7 of 7** planted engine defects detected (boundary, off-by-one, dedupe, timestamp, policy, …) | `experiments/results/differential_mutation_check.json`, `scripts/verify/mutation_check.py` |
| Batch = streaming under the lateness policy | 8 regimes (all five behaviours; the three windowed ones under both a generous and a tight 120 s lateness), 192 scenario runs, 0 disagreements; events the tight policy reports `late` are excluded exactly from the comparison | `streaming_agreement.json`, `scripts/verify/streaming_agreement.py` |
| Hard kill and restart | 3 of 3 kill points recover with no lost and no duplicated alert; a collector retry adds only `duplicate` records | `streaming_recovery.json`, `scripts/verify/streaming_recovery.py` |
| Correct at 36.7M events | **10,900 / 10,900** labelled incidents matched, 0 unexpected, 0 missing (re-verified on the current engine, 2026-09-24) | `experiments/results/phaseB_generated_dataset_check.json`; `sbt "runMain sentinelforge.compiler.GeneratedDataCheck …"` |

What none of that shows: correctness on **real** enterprise logs. The logs are synthetic; a real-data request to LANL is documented
in `docs/lanl-data-request.md` and has not been answered. See `docs/limitations.md`.

## 6. What is not done, and who has to do it

* A human second reviewer for both holdouts (the sheet and scorer are shipped; the author cannot be their own second reviewer).
* Feedback from a practising detection engineer. None has been collected, and none is claimed.
* The complete prompted-LLM row (needs a quota reset).
* A v3 holdout, to measure the post-v2 fixes honestly.
* Real-world logs.
