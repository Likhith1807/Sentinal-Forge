# Data Sources and Provenance (v1)

SENTINEL Forge's evaluation needs authentication telemetry at a scale the
48-event Phase 1 sample cannot provide. This document records where that
data comes from, under what terms, and — just as important — which parts
of it are real and which are synthetic. Every number reported from the
scaled dataset must be read against the "Real vs. synthetic" table below.

Surveyed 2026-09-22. Facts in the tables come from each source's own
documentation pages (linked); anything not confirmed from a primary source
is marked **unverified**.

## Requirements the data has to meet

The compiler reads the [authentication log schema](schema/authentication-log-schema.md)
(`account_id`, `timestamp`, `event_type`, `source_host`, `auth_method`,
`mfa_used`, optional `source_ip`/`session_id`) plus the
[account policy reference](schema/account-policy-reference.md)
(`expected_auth_method`, `mfa_required`) for B4/B5.

**No public dataset provides `mfa_used`, an `auth_method` in the
`password | token | certificate` split, or an account policy table.** No
public, labelled Entra ID / Okta sign-in dataset was found either (a
search returned only vendor documentation and training labs, not
downloadable data). A fully real dataset is therefore not available, and
this project does not pretend otherwise.

## Candidate sources

| Source | License | Content | Decision |
|---|---|---|---|
| [LANL Comprehensive Cyber-Security Events (cyber1)](https://csr.lanl.gov/data/cyber1/) | CC0 1.0; citation required when publishing results | `auth.txt`: `time, src user@domain, dst user@domain, src computer, dst computer, auth type, logon type, orientation, success/failure`. 1,648,275,307 events across all files (7.2 GB compressed), 58 days, 12,425 users, 17,684 computers. `redteam.txt` lists known red-team compromise events. Time is normalized epoch seconds; real dates withheld. | **Primary real background.** Access requires an emailed request (draft in [`lanl-data-request.md`](lanl-data-request.md), not yet sent). |
| [LANL Unified Host and Network Data Set (2017)](https://csr.lanl.gov/data/2017/) | CC0 | About 90 days of Windows host events incl. 4624/4625 with `LogonType`, `AuthenticationPackage`, `Status`. | Backup source. Its richer logon detail is not needed while `auth_method` is synthetic (see below). |
| [OTRF Security-Datasets](https://github.com/OTRF/Security-Datasets) | `LICENSE` file is MIT (2021, Open Threat Research Forge); the README text says GPL-3.0 — **inconsistent, must be resolved before use** | Simulated-attack Windows telemetry. | **Unverified** whether it contains logon / spray data (the catalogue page did not load). Manual browse required. Not used yet. |
| [EVTX-ATTACK-SAMPLES](https://github.com/sbousseaden/EVTX-ATTACK-SAMPLES) | GPL-3.0 | 200 `.evtx` samples with a Credential Access folder. | Private testing only. **Not vendored into this repository** (copyleft). |
| [Splunk attack_data](https://github.com/splunk/attack_data) | Apache-2.0 | Over 9 GB, organised by ATT&CK technique, Git LFS. | **Unverified** whether it has password-spray / brute-force / MFA data. Low priority. |

## Chosen design: real background + injected, labelled scenarios

1. **Background events** are real LANL authentication events, mapped into
   the project schema by [`scripts/datagen/lanl_mapper.py`](../scripts/datagen/lanl_mapper.py).
   Until LANL access is granted, a **synthetic benign background**
   ([`scripts/datagen/background.py`](../scripts/datagen/background.py))
   stands in so the whole pipeline is buildable and testable now.
2. **Injected incidents** for B1–B5
   ([`scripts/datagen/incidents.py`](../scripts/datagen/incidents.py)) are
   added on top: positives, exact-boundary positives, exact-boundary
   negatives, and hard negatives. Ground-truth labels come from how each
   incident was *constructed*, never from running a detector.
3. **Labels are cross-checked, not derived**, by a small pure-Python
   reference detector ([`scripts/datagen/refdetect.py`](../scripts/datagen/refdetect.py))
   that shares no code with the Scala compiler. Tests require that the
   detector agrees with every constructed label and raises no alert on the
   synthetic background alone.

## Real vs. synthetic (read every result against this)

| Element | With LANL background | Synthetic background (current) |
|---|---|---|
| Event volume and account/host structure | **Real** (LANL) | Synthetic |
| Timestamps | Synthetic mapping: LANL epoch-seconds offset from a fixed anchor date | Synthetic |
| `account_id`, `source_host` | **Real** (LANL identifiers, de-identified by LANL) | Synthetic |
| `event_type` | **Real** (success/failure) | Synthetic |
| `auth_method` | **Synthetic**, from the account's policy (LANL auth type and logon type are dropped) | Synthetic |
| `mfa_used` | **Synthetic**, from the account's policy | Synthetic |
| Account policy table | **Synthetic** | Synthetic |
| Injected B1–B5 incidents and their labels | Synthetic, by construction | Synthetic, by construction |
| LANL red-team events (secondary check) | **Real** labels, but semantics only loosely match B1/B3 — see below | n/a |

Any claim of the form "detects real attacks" is **not supported** by this
dataset and must not be made. What it supports: correctness on labelled
boundary cases, and throughput/latency at real-world volume and identity
structure.

### Why `auth_method` is synthetic even on real background

An earlier draft derived `auth_method` from LANL's authentication type
(Kerberos to `token`, everything else to `password`). That was dropped: LANL
accounts routinely mix Kerberos and NTLM, so any single expected method per
account would make B4 fire on a large share of real events with no label. The
background is instead policy-compliant by construction (successes use the
account's expected method and `mfa_used == mfa_required`), so B4/B5 alerts
come only from injected incidents. The real signal in the LANL background is
the identity structure, volume, timing and success/failure mix that B1-B3 and
the throughput measurements depend on.

## What "label" means at scale, and its limits

- **Injected incidents** have exact, independent labels, and each incident
  uses its own unique account/host, so incidents cannot interfere with one
  another. (This also sidesteps the known `DistinctCountWithinWindow`
  single-alert-per-group limitation in
  [`spec/detection-semantics.md`](spec/detection-semantics.md); that
  limitation remains open and is not fixed by the data.)
- **The synthetic background is constructed so it cannot trigger any of
  the five rules** (bounded failure bursts, one home host per account,
  policy-compliant methods and MFA), then verified by the reference
  detector.
- **Real LANL background will trigger some rules naturally** (an
  enterprise user touching two computers within 15 minutes is routine).
  Those alerts have **no independent label**. They must be reported as
  "unlabelled alerts on real background", sampled and adjudicated by hand,
  and never counted as false positives or true positives automatically.
  Precision on real background therefore requires manual adjudication of a
  sample.
- **LANL `redteam.txt`** contains `time, user@domain, source computer,
  destination computer` for known compromise events. It is used only as a
  secondary, real-data recall probe; it does not map cleanly onto B1–B3
  and does not label the absence of attacks.

## LANL format assumptions to verify against the real file

The mapper is built against LANL's documented field list, but the
following are **assumptions from the published dataset description that
have not been checked against the actual file** (access is pending):

- `orientation` uses `LogOn` for logon events; other orientations
  (`LogOff`, `TGS`, `TGT`, `AuthMap`, ...) are excluded by default.
- Values are `Success` / `Fail` for the status column, and `?` for missing.
- Machine accounts are identified by a trailing `$` in the user name and
  excluded by default.

The mapper takes these as explicit options and rejects unrecognised values
loudly instead of guessing; the first task after receiving the data is a
profile of each column's actual value set.

## Citation (required by LANL when publishing results)

Kent, A.D. (2015). *Comprehensive, Multi-Source Cyber-Security Events.*
Los Alamos National Laboratory. DOI: 10.17021/1179829.

## Terms of use

Only permitted, publicly licensed data and authorised lab telemetry are
used. LANL data is CC0. No GPL-licensed data is committed to this
repository. Generated detections require human approval before any
operational use (see the repository README).

## Measured generator and mapper performance

Measured on the development machine (16 logical CPUs, 16 GB RAM), single
process, 2026-09-22. These are indicative, not guarantees; the mapper number
is on a synthetic LANL-shaped file, not the real one.

| Component | Input | Result |
|---|---|---|
| `generate` (synthetic background + 10,900 incidents) | 200,000 accounts x 3 days | 2,657,608 events in 52 s (about 51,000 rows/s); 74.2 MB Parquet (snappy), about 28 bytes/row |
| Extrapolation | | 1 GB of Parquet is about 36M events (about 12 min); 10 GB is about 360M events (about 2 h). Generation is per-day and parallelisable. |
| `lanl_mapper` | 5,000,000 fake LANL-shaped rows (367 MB text) | about 131,000 rows/s |
| Extrapolation | | 1.648B real LANL rows in about 3.5 h single-process (unverified; real value distributions will differ) |

"Size" in later results must state whether it is Parquet-compressed bytes or
raw text/CSV-equivalent bytes; they differ by roughly an order of magnitude.

## Labels vs the real compiler at scale (2026-09-22)

The generated labels were checked against the actual Scala/Spark `RuleCompiler`, not just the Python
reference detector, on a dataset above the 1 GB target:

| | |
|---|---|
| Dataset | `--accounts 400000 --days 21 --incidents-per-behaviour 2000 --seed 42` (generator 1.0.0) |
| Events | **36,684,108** (36,648,015 synthetic background + 36,093 incident events), **1.055 GB** Parquet (snappy) |
| Labels | **10,900** (B1 2,000, B2 2,000, B3 2,000, B4 2,400, B5 2,500) |
| Reproducible | `labels.jsonl` sha256 `bf70d5b3...aa2e`, `policy.json` sha256 `3102e4ad...896b` (regenerating with the same seed must match; full hashes in the run's `manifest.json`) |
| Engine | Spark 3.5.3, `local[*]`, 10 GB heap, one machine |
| Result | **10,900 / 10,900 labels satisfied, 0 unexpected alerts, 0 missing alerts** |
| Compiler time | about 97 s for all five rules over the 36.7M events |

Every kind passed in every behaviour, including 870 exact-boundary positives and 869 exact-boundary
negatives across B1-B3 (per-kind counts in
[`experiments/results/phaseB_generated_dataset_check.json`](../experiments/results/phaseB_generated_dataset_check.json)).
"0 unexpected" is the strong half of that result: it means none of the 36.6M benign background events
triggered any rule.

**Negative control:** with one label deliberately corrupted, the same check reports 39/40, `unexpected=1`,
and exits non-zero, so a pass is capable of failing.

Reproduce (needs JDK 17, `HADOOP_HOME` and `.tools/hadoop/bin` on `PATH` on Windows; see
[`spec/stage4-scala-toolchain.md`](spec/stage4-scala-toolchain.md)):

```
python -m scripts.datagen.generate --out data/generated/scale_1gb --accounts 400000 --days 21 --incidents-per-behaviour 2000 --seed 42
sbt -Dsf.heap=10g "runMain sentinelforge.compiler.GeneratedDataCheck --dataset data/generated/scale_1gb"
```

What this does **not** show:
- **Synthetic background.** It shows the compiler is correct and scales to 36.7M events on one machine. It says
  nothing about behaviour on real traffic (the LANL background is still pending).
- **Incident entities are unique**, so `DistinctCountWithinWindow`'s known one-alert-per-group collapse
  ([`spec/detection-semantics.md`](spec/detection-semantics.md)) is deliberately not exercised here.
- **B2/B3 are matched on `groupKey`**, and B1/B4/B5 on triggering event + status. The check does not compare
  `detectedAt`/`matchedCount`.
- **One run.** Timing above is a single run, not a distribution (Phase E will report repeated runs).

## Reproducing

```
python -m scripts.datagen.generate --out data/generated/dev --accounts 20000 --days 7 --verify
python tests/datagen/test_generator.py
python tests/datagen/test_lanl_mapper.py
```

See [`scripts/datagen/README.md`](../scripts/datagen/README.md).
