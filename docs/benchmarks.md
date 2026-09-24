# Benchmarks

**Everything here was measured on one laptop, in one JVM, with Spark in `local[*]` mode. Nothing was measured on more than one machine, so
nothing here supports a claim about distributed scaling.** The numbers say how the engine behaves on this hardware and where it breaks
on this hardware. They are not a comparison with any other system.

Raw observations and computed summaries are committed under `experiments/results/benchmarks/`. Each summary embeds the hardware,
JVM settings, dataset shape, sample size and failure rate it was measured with.

## What was measured on

| | |
|---|---|
| Machine | 13th Gen Intel Core i7-13620H (10 cores / 16 logical CPUs), 15.6 GB RAM, NVMe SSD, Windows 11; 1 machine |
| Software | Spark 3.5.3, Scala 2.13.12, Java 1.8.0_491 (HotSpot 64-bit), Python 3.10.11; `local[*]`, 64 shuffle partitions |
| Engine version | commit `d514f57` for the batch, sweep and service results (the Scala engine did not change afterwards; later commits touched docs and scripts) |
| Data | generated, not real: `bench_small` 1,317,332 events (61 k accounts, 31 k hosts); `scale_1gb` 36,684,108 events (418 k accounts, 211 k hosts, 1.06 GB Parquet). Largest single account: 373 events (≈ 0.001 % of the data) — **there is no skew in this data** (a skew test is separate, §5) |
| Mode | every repetition re-reads the Parquet from disk (no caching) |

## How to read the statistics

* Only **warm, non-failed** repetitions enter a statistic; the first repetition of each behaviour is a recorded warm-up.
* A percentile is reported only when the sample supports it: **p95 needs n ≥ 20, p99 needs n ≥ 100**. With fewer repetitions the tables show
  the median and the min–max range. A "p95 of 3 runs" would be the maximum of three runs, and is not reported anywhere.
* Where a p95 is given, its bootstrap 95 % interval is in the JSON.
* Every result records **the number of attempts and failures**. Failures are counted, never dropped: in the heap sweep, two settings failed
  outright and are in the table.
* `resultRowsConsistentAcrossRuns` is checked on every run: repeated runs of one rule returned the same rows.

## 1. Batch, small data (1.3 M events; n = 30 warm runs per behaviour, 3 GB heap)

| behaviour | median | IQR | p95 (bootstrap 95 % CI) | events/s at median | peak heap | failed |
|---|---|---|---|---|---|---|
| repeated failed login → success | 4.94 s | 4.76–5.20 | 5.68 s (5.35–6.12) | 267 k | 2.1 GB | 0 / 31 |
| password spray across accounts | 4.67 s | 4.62–4.85 | 5.24 s (4.93–5.35) | 282 k | 2.4 GB | 0 / 31 |
| multi-host authentication | 5.15 s | 5.05–5.24 | 5.62 s (5.48–5.70) | 256 k | 2.9 GB | 0 / 31 |
| auth-method mismatch (policy) | 3.66 s | 3.61–3.75 | 3.94 s (3.78–4.00) | 360 k | 2.8 GB | 0 / 31 |
| MFA missing where required (policy) | 3.66 s | 3.60–3.77 | 4.18 s (3.85–4.21) | 360 k | 2.8 GB | 0 / 31 |

"Peak heap" is JVM heap occupancy including garbage that has not been collected yet; it is not a measure of live data.

## 2. Batch at scale (36.7 M events; n = 5 warm runs, so median and range only; 6 GB heap)

| behaviour | median | range | events/s at median | GC time (median) | failed |
|---|---|---|---|---|---|
| repeated failed login → success | 97.5 s | 95.6–99.2 | 376 k | 28.7 s | 0 / 6 |
| password spray across accounts | 68.8 s | 66.8–70.1 | 533 k | 12.7 s | 0 / 6 |
| multi-host authentication | 85.6 s | 84.7–87.7 | 428 k | 12.6 s | 0 / 6 |
| auth-method mismatch (policy) | 65.2 s | 64.1–74.8 | 562 k | 15.2 s | 0 / 6 |
| MFA missing where required (policy) | 66.8 s | 65.1–75.0 | 549 k | 16.9 s | 0 / 6 |

The same rules find **10,900 of 10,900** labelled incidents on this dataset (`GeneratedDataCheck`, run 2026-09-24 on the current engine).
Throughput is higher at 36.7 M than at 1.3 M events because fixed per-job costs (planning, scan set-up, shuffle set-up) are amortised.

### Heap sweep (password spray, 36.7 M events; `heap_sweep.json`)

| heap | outcome | median (range) | GC time |
|---|---|---|---|
| 2 GB | **failed** — `OutOfMemoryError`, JVM exit 52, no run completed | — | — |
| 3 GB | **failed** — `OutOfMemoryError`, JVM exit 52, no run completed | — | — |
| 4 GB | completed, **memory-bound** | 175.1 s (163.0–181.7) | 112.5 s |
| 6 GB | completed | 68.8 s (66.8–70.1) | 12.7 s |
| 8 GB | completed | 68.1 s (68.0–72.6) | 13.3 s |

On this dataset the batch job needs about 5 GB of heap to run without being dominated by garbage collection. The 4 GB run is 2.5× slower and spends
64 % of its time in GC. More than 6 GB buys nothing. (n = 3 warm runs per setting: medians and ranges, no percentiles.)

### Worker sweep (password spray, 1.3 M events, 10 warm runs per setting, 3 GB heap)

| `local[N]` | 1 | 2 | 4 | 8 | 16 (`local[*]`, n = 30) |
|---|---|---|---|---|---|
| median | 13.1 s | 8.2 s | 5.5 s | 4.0 s | 4.7 s |

Scaling is sub-linear (3.3× at 8 threads) and **16 logical CPUs were slower than 8** on this 10-core machine. That is a fact about this laptop
(hybrid cores, shared cache and memory bandwidth), not a claim about any cluster.

## 3. Cold versus warm

| what | time | note |
|---|---|---|
| JVM start → Spark session ready | 4.7–5.8 s | one machine, SSD |
| first full Parquet scan | 1.0–1.3 s | |
| first repetition vs warm median (1.3 M events, spray) | 6.1 s vs 4.7 s | JIT and file-cache warm-up |
| **a run submitted through the service, fresh JVM per run** (725-event demo dataset, n = 8) | **18.0 s** (17.8–18.2) | dominated by JVM and Spark start; the data is tiny |
| the same run on the reference engine (pure Python, n = 30) | **0.03 s** (0.025–0.107) | no JVM; both returned the same 5 alerts |

Because of that gap, the service's default (`SF_ENGINE=auto`) runs a **small JSONL dataset (up to 5 MB, `SF_REFERENCE_MAX_BYTES`) on the reference engine** and anything larger, or any Parquet,
on Spark when a JVM is available; `spark` or `reference` can be forced. The two engines are compared for equality by the differential suite, and every run record
names the engine that produced it. The 18 s is honest cold-start cost, not a measurement of the engine's speed.

## 4. Streaming latency

*Method* (`scripts/bench/streaming_latency.py`): one local JVM; trigger interval 1 s; a driver writes one immutable file per step (atomic rename)
and records the wall-clock time; an alert is **decidable** once the key has seen an event at least `lateness` newer than the triggering
event (the engine finalises events in timestamp order — that is the price of matching batch exactly), so per incident the driver sends that
closing event; **latency = the alert file's modification time − the arrival of the event that made the alert decidable**. 100 incidents per
configuration, 2 s apart, clock started only after the query had processed its first batch (start-up 16.2–16.7 s before that, reported
separately). 100 of 100 alerts were matched in every configuration (0 missing).

| behaviour | lateness | median | IQR | p95 | p99 | max |
|---|---|---|---|---|---|---|
| repeated failed login → success | 0 s | 2.40 s | 1.99–2.78 | 3.30 | 3.44 | 3.45 |
| | 5 s | 2.37 s | 1.98–2.64 | 2.92 | 3.02 | 3.04 |
| | 30 s | 2.48 s | 1.97–2.74 | 3.10 | 3.23 | 3.24 |
| password spray across accounts | 0 s | 2.54 s | 1.99–2.91 | 3.13 | 3.30 | 3.35 |
| | 5 s | 2.35 s | 1.96–2.76 | 3.16 | 3.45 | 3.50 |
| | 30 s | 2.43 s | 2.00–2.73 | 2.98 | 3.23 | 3.36 |

n = 100 per row, so p95 is supported and p99 is at the edge of what 100 samples can say (it is the 99th of 100 values). What this is: the engine's own micro-batch
and commit latency **once the delay policy is satisfied** — a bit over one trigger interval plus about a second of processing. What it
is **not**: it excludes the configured `lateness` (an alert cannot be released before an event `lateness` newer arrives — that delay is a
policy, and it is the price of exact agreement with batch), source transport, and any cluster.

*An earlier run of this benchmark is kept and marked superseded* (`streaming_latency_run1_with_standby.json`). It showed two alerts at
about 1,458 s and 1,460 s: the laptop entered Modern Standby for 24.2 minutes (Windows Kernel-Power events 506/507 at 08:49:48 and 09:14:01)
and stalled the JVM and the driver together. It also showed a staircase of 8–22 s for the first ten incidents of one configuration, because the driver
slept a fixed 12 s before starting and the JVM had not finished starting. The re-run gates on the first processed batch and holds the machine awake
(`scripts/bench/keep_awake.py`). The outliers were the measurement's fault, not the engine's, and they were removed by fixing the measurement — not by
trimming the data.

## 5. Streaming: many keys, and one very hot key

`scripts/bench/streaming_stress.py`; each run is checked against the exact number of alerts it must produce.

| shape | events | keys | wall time (incl. 16 s JVM start) | events/s end-to-end | alerts (expected) | duplicates |
|---|---|---|---|---|---|---|
| **high cardinality** — 200 k accounts with a partial pattern, 1 k with a full one | 404,000 | 201,000 | 26.1 s | 15.5 k | 1,000 (1,000) | 0 |
| **hot key** — one host failing against 25 k distinct accounts | 30,000 | 1,001 | 26.1 s | 1.1 k | 1,001 (1,001) | 0 |
| hot key, 50 k | 55,000 | 1,001 | 37.7 s | 1.5 k | 1,001 (1,001) | 0 |
| hot key, 100 k | 105,000 | 1,001 | 58.4 s | 1.8 k | 1,001 (1,001) | 0 |
| hot key, 200 k | 205,000 | 1,001 | 147.9 s | 1.4 k | 1,001 (1,001) | 0 |

Both shapes are **correct** (exact alert counts, no duplicate alerts, no `late` records). The hot key is the design's honest limit: one key's
window history is proportional to the events inside its window, and one key is processed by one task, so throughput on a hot key
is roughly an order of magnitude lower (1–2 k events/s) than across many keys, and a very large hot key runs for minutes. Time grows about
linearly from 25 k to 100 k events per key, then faster at 200 k. Adding machines would not help: it is one key. (The state-store memory metric that Spark
reports for these runs is not reliable here — it is far smaller than the data, so it is left in the JSON and **not** quoted.)

## 6. Batch versus streaming

| | batch (Spark) | streaming (Spark Structured Streaming) |
|---|---|---|
| 1.3 M events, all keys | 3.7–5.2 s per rule, warm | not measured as a bulk job — not what it is for |
| a single alert, once decidable | n/a (results after the whole job) | median ≈ 2.4 s, max 3.5 s |
| correctness | reference-checked | equal to batch under a lateness policy (8 regimes, 192 scenarios, 0 disagreements) |
| a hot key | handled in one pass | slow but correct (§5) |

They answer different questions: batch is for scanning history; streaming is for time-to-alert. Their per-event throughputs are **not** comparable
(streaming here re-reads small files with a 1 s trigger and includes JVM start-up).

## 7. What was not measured

* **Any distributed run.** One machine. No claim.
* Real data. The data is generated; a real key distribution would probably be more skewed than the near-uniform data in §1–2.
* Concurrent users on the service, or several jobs at once.
* Streaming from a real message bus (files stand in for it).
* Memory of the Python service process.
* Cost.

## Reproduce

```
# small data, 30 repetitions per behaviour (about 12 minutes)
python scripts/bench/run_batch.py --dataset data/generated/bench_small --generate --reps 30 --heap 3g --name bench_small
# large data (needs the 1 GB dataset: python -m scripts.datagen.generate ... ; about 40 minutes)
python scripts/bench/run_batch.py --dataset data/generated/scale_1gb --reps 5 --heap 6g --name scale_1gb
# streaming
python scripts/bench/keep_awake.py &            # Windows laptops: do not let the machine sleep mid-run
python scripts/bench/streaming_latency.py --incidents 100 --lateness 0,5,30
python scripts/bench/streaming_stress.py
python scripts/bench/service_latency.py
```

Close other heavy programs first; the runs above were made on an otherwise idle machine. Results on another machine will differ; the harness
records that machine's hardware in the file so a reader can see what a number was measured on.
