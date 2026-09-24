# SENTINEL Forge

![CI](https://github.com/Likhith1807/Sentinal-Forge/actions/workflows/ci.yml/badge.svg)

**A threat-report-to-detection compiler that is built to say "I don't know".**
Paste a paragraph of threat-report prose; get a Spark detection rule in which every number, unit and field is tied to an exact quote from the
report — or a refusal that names the sentence that caused it. A language model may *propose*; only evidence in the text may *authorise*.

```
 "…alert when an account has 5 or more failed logins inside a 2-minute window, then a successful login…"
                                        │
        ┌───────────────────────────────┼─────────────────────────────────────┐
        ▼                               ▼                                     ▼
  count ≥ 5  (quote @ 457–466)   window 2 min = 120 s (quote @ 490–498)   "14" elsewhere: narrative, not used
        └───────────────────────────────┼─────────────────────────────────────┘
                                        ▼
        compiled rule v1 · hash 9f67530e… · run on a dataset version · alerts with evidence · approved by an analyst
```

## See it in 3 minutes

```
python scripts/demo.py                                   # one detection · one justified refusal · one schema-change failure (no JVM needed)
python -m uvicorn dashboard.backend.main:app --port 8000 # the five-screen workflow: http://127.0.0.1:8000
```

[`docs/demo-script.md`](docs/demo-script.md) is the recording script; [`docs/case-study.md`](docs/case-study.md) is the story of how the design was
forced by an audit of the first version.

## Why it is built this way

An audit of my own first pipeline found that it compiled only **27 of 40** supported reports correctly — and, worse, produced **7 silent failures**:
"90 seconds" compiled to a 90-*minute* window, an ambiguous count was read as a different behaviour, and a port-scan report was accepted as a
supported one. Each would have shipped a wrong detection with a green tick. So the language model was demoted from decision-maker to
proposer, and everything else was built to make failure visible:

| principle | mechanism |
|---|---|
| Nothing is asserted without a quote | a deterministic condition finder records every candidate value with its offsets; `reconcile()` returns *accepted / rejected / needs review* with reason codes |
| A model can never make an unsupported value acceptable | "90 seconds" cannot become 90 minutes: the text has to say "minutes" |
| Refuse rather than guess | contradictions, hedges ("possibly 15"), corrections, ranges, ambiguous count semantics and qualifiers no recipe can evaluate all go to review or rejection |
| "Correct" is defined and checked twice | `docs/spec/detection-semantics.md` implemented in Spark **and** an independent Python reference engine, compared on generated scenarios; a mutation check confirms the comparison catches 7 of 7 planted bugs |
| The data can change | dataset versions + schema-change impact analysis: remove `source_host` and the spray rule is paused, the brute-force rule keeps running |
| Evaluate on data nobody tuned against | SHA-256-frozen, git-tagged holdouts, one run, failures published |

## Results (what was measured; every line has a command and a file)

| claim | result | where |
|---|---|---|
| The audit's silent failures are closed | **7 → 0** silent failures on the 44-report regression split; 27/40 → 38/40 correct; every defect has a regression test | [`docs/evaluation.md` §1](docs/evaluation.md) · `tests/core/test_audit_regressions.py` |
| Frozen holdout v1 (112 reports, 16 real CISA/FBI passages) | raw fine-tuned model: **32% silently wrong**, accepts 56% of must-refuse reports; with the evidence check: **0% silent errors** (at the cost of refusing many) | [`docs/evaluation.md` §2](docs/evaluation.md) |
| Frozen holdout v2 (71 fresh reports, written after the fixes) | evidence-only default: **63%** of supported reports compiled correctly (v1: 44%), **0 silent errors**, 5 of 41 must-refuse reports wrongly accepted — published, then fixed with tests, and marked no-longer-held-out | [`docs/evaluation.md` §3](docs/evaluation.md) · `experiments/results/holdout_v2/POST_HOC.md` |
| Correct at 36.7M events | **10,900 / 10,900** labelled incidents matched exactly (0 unexpected, 0 missing) on a generated 1 GB Parquet dataset | `experiments/results/phaseB_generated_dataset_check.json` |
| Spark = independent reference engine | **750 generated scenarios, 0 mismatches** (all five behaviours) | `experiments/results/differential_large.json` |
| The test of the tests | differential harness detects **7 / 7** planted engine defects | `experiments/results/differential_mutation_check.json` |
| Batch = streaming under a lateness policy | 8 regimes, 192 scenarios, 0 disagreements; hard-kill and restart at 3 points loses and duplicates nothing | `streaming_agreement.json`, `streaming_recovery.json` |
| Performance | measured on one laptop with hardware, heap, dataset shape, sample sizes and failure rate stated; **no distributed claim** | [`docs/benchmarks.md`](docs/benchmarks.md) |
| Test suite | 432 pytest cases (+ 7 Spark integration tests and 14 ScalaTest cases), no API key or GPU required | `pytest` · `pytest -m integration` · `sbt test` |

**What these do not show** — in full in [`docs/limitations.md`](docs/limitations.md): the data is synthetic (a real-data request to LANL is drafted, not answered);
recall on unfamiliar wording is modest (63% / 44%); the holdouts are small and labelled by one person plus, for v1, a blind LLM reviewer (a human
second reviewer is still open); the prompted-LLM comparison is *partial* (provider quota) and labelled so; no practitioner has reviewed the tool;
everything was measured on one machine.

## The workflow (five screens)

**Overview → Report workspace → Validation review → Investigation → Evaluation.** Paste a report; the analysis runs as a background job with a visible
state; inspect each extracted condition beside its highlighted quote; check it against the *dataset's* schema; compile a versioned rule; run it on a fresh
job; inspect matching events and each alert's six-question evidence record (*why, means, supported, fired, executed, uncertain*); approve **that rule
version**. Results are always tied to report + rule version + dataset version + run and labelled *fresh / archived / demo*. Details: [`dashboard/README.md`](dashboard/README.md).

## Architecture in one glance

```
report ─▶ conditions.py (finder, quotes+offsets) ─▶ reconcile.py (accepted | needs_review | rejected)
      ─▶ validation.py + schema_impact ─▶ compile.py (typed spec, rule hash)
      ─▶ RuleCompiler.scala / RunRule (batch)   ·   StreamingEngine.scala (lateness, dedupe, expiry, policy versions)   ·   refengine.py (reference)
      ─▶ service (SQLite WAL, job states, rule versions, dataset versions, evidence) ─▶ FastAPI + UI
```

Full description: [`docs/architecture.md`](docs/architecture.md). Detection semantics: [`docs/spec/detection-semantics.md`](docs/spec/detection-semantics.md).

## Run it

```
pip install -r requirements.txt                     # pinned; no GPU, no API key
python scripts/demo.py                              # the three moments, in a terminal
python -m uvicorn dashboard.backend.main:app        # the UI (loopback only, no login, in local-demo mode)
pytest                                              # 432 tests, ~1 min
pytest -m integration                               # Spark agreement (needs JDK 8–17 and sbt)
docker compose up --build                           # two-target image (slim: reference engine; full: Spark)
```

Reproduce the evaluation: `python scripts/holdout/verify_frozen.py` and `python experiments/holdout/run_eval.py --skip-llm`
(add `--holdout holdout_v2` for v2). Reproduce the benchmarks: [`docs/benchmarks.md`](docs/benchmarks.md). Deployment and security modes: [`docs/deployment.md`](docs/deployment.md).

## Repository map

| path | contents |
|---|---|
| `sentinelforge/` | behaviour registry, condition finder, reconcile, validation, compile, reference engine, Sigma export, engine launchers |
| `sentinelforge/service/` | store, workflow, jobs, datasets, schema impact, evidence records, extractors |
| `compiler/` | Scala: rule compiler, batch and streaming engines, checks, benchmark harness (`sbt test`) |
| `dashboard/` | FastAPI backend and a build-step-free five-screen frontend |
| `nlp/` | extractors (classical, fine-tuned RoBERTa, prompted LLM) and the training pipeline |
| `data/` | demo dataset and reports, the synthetic corpus, the frozen holdouts (`holdout/`, `holdout_v2/`) with hashes and reviews |
| `experiments/` | audit, holdout evaluation, and every archived result as JSON |
| `scripts/` | verification (differential, mutation, streaming), benchmarks, data generation, packaging |
| `docs/` | architecture, evaluation, limitations, benchmarks, case study, specs (some phase-era documents are marked *Historical*) |

## Scope

Five observable authentication behaviours, one documented log schema, three closed recipes. Only permitted reports and authorised lab telemetry
are used. A generated detection is a *draft* until a person approves it — the approval flow is the point of the product, not a flourish.
