# scripts/datagen — scaled, labelled authentication data

Builds the dataset the Phase 5 evaluation runs against. Read
[`docs/data-sources.md`](../../docs/data-sources.md) first: it states what is
real and what is synthetic, and every result must be read against that.

## Generate a dataset

```
python -m scripts.datagen.generate --out data/generated/dev --accounts 20000 --days 7 --verify
```

| Option | Meaning |
|---|---|
| `--accounts N` | synthetic background accounts (about 4.4 events per account-day) |
| `--days N` / `--start-date` | date range of the background and of incident placement |
| `--incidents-per-behaviour N` | incidents per behaviour, cycling evenly through every kind |
| `--seed N` | full determinism: same seed gives byte-identical labels and events |
| `--verify` | cross-check every label with the independent reference detector (up to 3M rows) |
| `--background-dir` / `--background-policy` | inject into an existing partitioned background (e.g. LANL mapper output) instead of generating one |

Output under `--out`: `events/event_date=*/` (Parquet, Hive layout, same columns as
`data/processed/events` minus its `source_scenario` provenance column), `labels.jsonl`, `policy.json`, `manifest.json`.

## Label format (`labels.jsonl`)

One JSON object per incident. `expectedStatus` is `alert`, `no_alert` or
`insufficient_context`; `kind` is one of `positive`, `boundary-positive`,
`boundary-negative`, `hard-negative`, `insufficient-context`. `eventIds`
lists every event of the incident and `expectedTriggerEventId` the event an
alert must fire on. Each incident uses accounts and hosts no other incident or
background event uses.

An account with no policy row makes *both* B4 and B5 degrade to
`insufficient_context`, so that incident carries two labels (one per behaviour).

## Map LANL data

```
python -m scripts.datagen.lanl_mapper --auth data/external/lanl/auth.txt.gz \
    --out data/generated/lanl --redteam data/external/lanl/redteam.txt.gz
python -m scripts.datagen.generate --out data/generated/lanl_labelled \
    --background-dir data/generated/lanl/events --background-policy data/generated/lanl/policy.json --verify
```

The mapper records a value profile of LANL's categorical columns in
`mapper_manifest.json`; check it against the assumptions in `docs/data-sources.md`
before trusting a full run. (Not yet run on real data: access is pending, see
[`docs/lanl-data-request.md`](../../docs/lanl-data-request.md).)

## What is deliberately not injected

A `NULL` `mfa_used` or `auth_method` on a success. The compiler currently
scores that as `no_alert` rather than `insufficient_context`
([`detection-semantics.md`](../../docs/spec/detection-semantics.md), case G);
injecting it now would encode a known gap as an expectation. It is added once
that gap is fixed.

## Tests

```
python tests/datagen/test_generator.py
python tests/datagen/test_lanl_mapper.py
```

Includes a mutation test: a boundary incident nudged one second across its
boundary must make the reference detector disagree with the stale label.

## Layout

`params` (thresholds read from the compiled specs) · `policy` (deterministic
synthetic policy) · `background` (vectorised benign traffic) · `incidents`
(labelled B1-B5 builders) · `refdetect` (independent cross-check) · `generate`
(CLI) · `lanl_mapper`.
