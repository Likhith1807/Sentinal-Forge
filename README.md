# SENTINEL Forge

![CI](https://github.com/Likhith1807/Sentinal-Forge/actions/workflows/ci.yml/badge.svg)

Evidence-grounded threat-report-to-detection compiler. Takes a permitted threat report, extracts
the attacker behaviour it describes, checks whether the documented log schema can actually observe
it, and — only if it can — emits a typed Scala/Spark detection rule. Every claim below is measured
and reproducible, not asserted; the commands to reproduce each one are next to it.

## Results

| Claim | Result | How to reproduce |
|---|---|---|
| The compiler is correct at real scale | **10,900 / 10,900** labelled incidents matched exactly, on a real **36.7M-event** dataset (1.055 GB Parquet) | `sbt "runMain sentinelforge.compiler.GeneratedDataCheck --dataset data/generated/scale_1gb"` |
| The compiler agrees with an independent, non-Spark reference implementation | **1,500 / 1,500** random Hypothesis-generated scenarios agree, across all 5 behaviours | `python compiler/test/differential_property_test.py` |
| A fine-tuned extractor beats a prompted LLM and a classical baseline | Behaviour accuracy: **fine-tuned 0.955**, hybrid 0.975, prompted (`gpt-oss-120b`) 0.909, classical 0.205 — real 4-system comparison, n=44, bootstrap 95% CIs | `python nlp/src/evaluate_corpus.py` — see [`docs/phase-c-extraction.md`](docs/phase-c-extraction.md) |
| Streaming detection survives a real restart | **PASS** — kill a live Structured Streaming query, restart it against the same checkpoint, get the exact same result as an uninterrupted batch run, no duplicates | `sbt "runMain sentinelforge.compiler.StreamingRecoveryCheck"` |
| Exact vs. approximate counting matters at real scale | The hand-written baseline's `approx_count_distinct` misclassifies **6 of 2,000** incidents (0.3%) — invisible at 48-event scale, real at 36.7M | [`docs/phase-e-scale.md`](docs/phase-e-scale.md) |
| The whole test suite is real and fast | **75 pytest cases + 7 ScalaTest cases**, roughly 20-65s total on one machine (real observed variance, not a single-run number), no API key or GPU required | `pytest -v && sbt test` |

**What these numbers don't claim**: the background data is synthetic (real LANL enterprise data is
requested but not yet received — [`docs/data-sources.md`](docs/data-sources.md)); the report corpus
is synthetic too ([`docs/corpus.md`](docs/corpus.md)); throughput is measured on one machine, not a
cluster ([`docs/phase-e-scale.md`](docs/phase-e-scale.md)). Every one of these limits is stated in
the doc it belongs to, not just here.

## How it works

```
 threat report (.md)
        |
        v
 [1. Understand]   NLP extraction — classical regex, a prompted LLM, and a fine-tuned
                    transformer (nlp/src/) — produce a typed behaviour spec with
                    character-offset evidence for every field, never asserted without a quote.
        |
        v
 [2. Validate]      Stage 3 (compiler/src/observability_checker.py) checks the spec against
                    the real, documented log schema. A behaviour needing a field the schema
                    can't provide is REJECTED here, not silently compiled.
        |
        v
 [3. Compile]       spec_bridge.py fills one of 3 closed recipe shapes (SequenceThenTrigger,
                    DistinctCountWithinWindow, PolicyCompare) with the extracted numbers.
                    RuleCompiler.scala emits real Spark SQL — structurally immune to
                    hardcoding or hallucinated enum literals by construction.
        |
        v
 [4. Execute]       Runs on partitioned Parquet (batch) or Structured Streaming (the one
                    recipe Spark supports incrementally — docs/spec/stage4-streaming-and-sigma.md).
        |
        v
 [5. Validate]      Replayed against independently-labelled events. Every alert traces back
                    to a real event id and the report evidence that justified it.
        |
        v
 [6. Review]        Analyst dashboard: approve / refine, with the evidence and Stage 3
                    verdict shown live (dashboard/README.md).
```

## Quickstart

```
python scripts/demo.py                          # or: make demo
# Fast, offline, no API key, no JVM — extracts a real report, validates it, and
# prints the real, already-computed results from the checks above.

pytest -v && sbt test                           # or: make test
# 75 + 7 real tests, roughly 20-65s combined (varies with machine load), no external dependencies.

docker compose up --build                       # or: make docker-up
# Live analyst dashboard at http://localhost:8000 (GROQ_API_KEY optional — only
# needed for the live prompted-extraction panel; see .env.example).
```

To put the dashboard behind a public URL (Render or Fly.io, both config files included) and
record a demo walkthrough, see [`docs/deployment.md`](docs/deployment.md) and
[`docs/demo-script.md`](docs/demo-script.md).

## Repository layout

- `nlp/` — extraction: classical/prompted/fine-tuned extractors, the training pipeline, and their
  real comparison ([`docs/phase-c-extraction.md`](docs/phase-c-extraction.md))
- `compiler/` — the typed IR, Stage 3 validation, `spec_bridge.py`, and the Scala/Spark compiler
  (`compiler/src/main/scala`) with its ScalaTest suite (`compiler/src/test/scala`)
- `scripts/datagen/` — the labelled, scaled synthetic dataset generator and LANL mapper
  ([`docs/data-sources.md`](docs/data-sources.md))
- `scripts/corpus/` — the 201-report synthetic corpus generator, with a guarded LLM-rewrite tier
  and a leakage gate ([`docs/corpus.md`](docs/corpus.md))
- `dashboard/` — the analyst review web app (FastAPI + vanilla JS, no build step)
- `experiments/` — baselines (manual rules, direct-LLM, schema-constrained) and every measured
  result, as JSON, under `experiments/results/`
- `docs/` — design specs, the annotation schema, and every phase's real results and limitations
- `tests/`, `compiler/test/`, `nlp/test/` — the test suite (`pytest`/`sbt test` collect all of it —
  see [Testing](#testing) below)

## Testing

`pytest -v` (75 cases, 21-64s observed) and `sbt test` (7 cases, ~28s observed) each run in well under 2 minutes, need no API key,
no GPU, and no generated dataset. `pyproject.toml` scopes `pytest` to the real unit tests and
excludes real-API evaluation scripts (`evaluate_*.py`, the differential property test, adversarial
extraction fixtures) from routine runs — those are separately-run evaluations with their own real
results already committed under `experiments/results/`, not something a CI run re-executes on
every push. CI (`.github/workflows/ci.yml`) runs both suites on every push — see the badge above.

The larger, real-scale checks (`GeneratedDataCheck` on 36.7M events, `ScaleBenchmarkCheck`,
`differential_property_test.py` at full scenario count) are run manually, documented with their
real results in `docs/`, and are too heavy for routine CI — see
[`docs/spec/stage4-scala-toolchain.md`](docs/spec/stage4-scala-toolchain.md) for how to run them
yourself.

## Honest limitations, by document

- **Data is synthetic.** [`docs/data-sources.md`](docs/data-sources.md): the background traffic is
  generated, not real enterprise data (a request for real LANL data is drafted, not sent/approved).
- **The report corpus is synthetic.** [`docs/corpus.md`](docs/corpus.md): no independent human
  annotation pass has confirmed the gold yet; the sample sheet and scorer are built.
- **Compiler semantics have documented, real edge cases**, fixed where found:
  [`docs/spec/detection-semantics.md`](docs/spec/detection-semantics.md).
- **An independent review found real bugs across two rounds**, all reproduced and fixed with
  evidence: [`docs/spec/independent-review-corrections.md`](docs/spec/independent-review-corrections.md).
- **Scale is measured on one machine**, not a cluster; a 2-point curve, not a smooth one:
  [`docs/phase-e-scale.md`](docs/phase-e-scale.md).
- **The prompted-LLM extractor doesn't abstain reliably** (0.0 recall on out-of-scope reports, at
  two model sizes tested) — a real finding, not smoothed over:
  [`docs/phase-c-extraction.md`](docs/phase-c-extraction.md).

## Scope

Five observable authentication behaviours, one documented log schema, three closed detection
recipes. Only permitted threat reports and authorised lab telemetry are used. Generated detections
require human approval before any operational use — the dashboard's approve/refine flow exists
specifically for that gate, not as a demo flourish.
