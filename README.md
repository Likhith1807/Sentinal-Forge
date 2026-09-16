# SENTINEL Forge

Evidence-grounded threat-report-to-detection compiler for the combined Text Analytics and Big Data Analytics project.

## Core workflow

1. **Understand** — extract attacker behaviour, entities, conditions and event sequences from permitted threat reports.
2. **Compile** — verify that the documented log schema can observe the behaviour, then emit a typed Scala/Spark detection rule.
3. **Validate** — replay independently labelled events, measure detection quality, and present supporting evidence and limitations for analyst approval.

## Initial scope

- One documented security-log schema
- Five observable behaviours
- Python/PyTorch NLP training pipeline
- Scala + Apache Spark compiler and execution pipeline
- Partitioned Parquet + Spark SQL analytics
- Replay pipeline and review dashboard

## Repository layout

- `data/` — raw, processed and sample data
- `nlp/` — text preprocessing, extraction models and notebooks
- `spark/` — Scala/Spark ETL, streaming and configuration
- `compiler/` — typed detection specification and rule compiler
- `dashboard/` — analyst review interface and API
- `experiments/` — baselines, ablations and measured results
- `tests/` — cross-component tests
- `docs/` — design, schema and evaluation documentation
- `scripts/` — repeatable setup and execution scripts

## Evaluation plan

Compare manual rules, direct LLM generation, schema-constrained generation and SENTINEL Forge. Report extraction F1, rule correctness, detection recall, false-positive rate, abstention coverage, throughput, p95 latency and failure recovery. Keep report families and near-duplicates out of held-out test splits.

Only use permitted threat reports and authorised lab telemetry. Generated detections require human approval before any operational use.
