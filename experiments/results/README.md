# Phase 5 — Evaluate & Explain

Every number below comes from an actual execution against the real
partitioned Parquet store (`data/processed/events/`) and the independently
authored labels in `data/samples/replay/`. Nothing here is estimated or
asserted — see "What's honestly still missing" at the end for what a fully
exhaustive Phase 5 would still need.

## Summary

| System | Behaviours | Real result | Key real finding |
|---|---|---|---|
| Manual rules | 5/5 hand-written | **17/17** (after a real bug fix — see below) | Had the identical timestamp-parsing bug as the untested Phase 0 golden reference; never caught across 4 phases because the code was never executed until now |
| Direct LLM generation | 5/5 generated, all compile | **2/5 behaviours correct** (see per-behaviour table) | 2 of 5 hardcode the one account name from the source report as a fake policy table; the other 3 range from perfect to badly duplicated |
| Schema-constrained generation | 5/5 generated + 1 correct rejection, all compile | **0/5 behaviours fully correct** | 2 files reference wrong enum literals (`"failure"` instead of `"login_failure"`) that Stage 3's field-existence check cannot catch; 1 hardcodes an account like direct-LLM; 1 has an unresolved timestamp-arithmetic bug |
| SENTINEL Forge (full) | 5/5 via deterministic compiler | **17/17**, plus 2/2 robustness tests passed | Not LLM-generated — a closed 3-recipe interpreter, structurally immune to hardcoding and enum-literal hallucination by construction |

## Full per-behaviour execution results (all 10 held-out-generated files, run for real)

| Behaviour | Direct LLM | Schema-constrained |
|---|---|---|
| B1 login-brute-force | ✅ 2/2 true positives, 0 false positives | ❌ 0 alerts — filters on `event_type === "failure"`/`"success"` instead of the real `login_failure`/`login_success` |
| B2 password-spray | ⚠️ Correctly identifies the incident, but as **7 duplicate overlapping alerts** for 1 true positive (sliding-window artifact, confirming the hypothesis flagged in `nlp/README.md`) | ❌ 0 alerts — same wrong-enum-literal bug as B1 |
| B3 concurrent-sessions | ❌ 0 alerts — self-join produces no matches (likely a string/timestamp type-coercion issue in the `timestamp + interval` arithmetic against a raw string column; not fully root-caused, flagged with that caveat rather than overclaimed) | ❌ 0 alerts — same self-join, same likely cause |
| B4 service-account-auth | ❌ 0 alerts — hardcodes `Map("svc-notify09" -> "token")`, the one account from its source report | ❌ 0 alerts — hardcodes `Set("svc-notify09")`, same pattern |
| B5 mfa-bypass | ❌ 0 alerts — hardcodes `Set("dpatel")`, the one account from its source report | ⚠️ **8 alerts, only 1 correct** — lost its policy check entirely (extraction omitted the policy field this run), alerts on unrelated MFA-less logins from B1 and B4's own events |

Only B1's direct-LLM output is a genuinely correct, usable rule. Every
other cell is a real, distinct failure mode — not the same bug five times.

## The bug that made "manual rules" a fair, not favorable, comparison

`ManualBaselineCheck` initially scored 15/17, failing exactly the two
time-window-precision edge cases (B1 scenario E, B3's CS-NEG-WINDOW) — the
scenarios Phase 1's replay design specifically built to catch this class of
error. Root cause: `$"timestamp".cast("long")` casts the raw ISO-8601
*string* straight to long instead of parsing it, which Spark accepts
silently and returns `null` for every row — breaking the window's
`ORDER BY` and making `rangeBetween` effectively unbounded. This exact bug
had been sitting in the Phase 0 golden reference (and everything that
copied its pattern) since day one, invisible because none of that code was
ever actually run until Phase 4. Fixed in `LoginBruteForceThenSuccess.scala`,
`ConcurrentSessionsDifferentHosts.scala`, and `PasswordSprayAcrossAccounts.scala`.
Re-run: **17/17**.

Left as an in-repo bug-fix, not silently corrected: a systematically
generated rule (`RuleCompiler.scala`, written after this bug was already
understood) got the timestamp parsing right *because* writing it forced
explicit reasoning about the schema; three independently hand-written
files, written earlier under the same "this looks right" assumption, all
made the identical mistake.

## Robustness (SENTINEL Forge's own compiled rules under data stress)

`RobustnessCheck.scala` — 2 real tests:

1. **Policy source unavailable.** Ran `mfa-bypass-on-required-account`
   twice against the same events: once against the real policy table,
   once against an emptied one (simulating the policy source being down).
   Result: every account that previously got `alert` or `no_alert`
   correctly degraded to `insufficient_context` — **no false alerts, no
   silently-wrong compliant readings.**
2. **A required field disappears entirely.** Dropped `source_host` from
   the events schema and tried to compile `password-spray-across-accounts`
   against it. Result: a specific, named
   `UNRESOLVED_COLUMN.WITH_SUGGESTION` exception identifying exactly the
   missing column — fails loudly and specifically, not silently.

Both are genuinely positive, measured findings for the deterministic
compiler design, not assumed.

## Ablation: Stage 3 gate ON vs OFF (a true controlled ablation)

`ablation_stage3_gate.py` runs the *same* extraction (transformer) through
two conditions differing in exactly one thing — whether Stage 3's verdict
gates generation — across the 5 real behaviours plus the geo-anomaly
adversarial fixture. Real result:
[`phase5_ablation_stage3_gate.json`](phase5_ablation_stage3_gate.json).

**Diverges in exactly 1 of 6 cases.** On all 5 real behaviours, gate ON
and OFF produce identical outcomes — none of them needed rejecting, so
there was nothing for the gate to change. The one case that diverges is
the adversarial fixture, and the artifact it produces without the gate is
more interesting than a false positive would have been:
[`ablation_geo_anomaly_gate_off.scala`](ablation_geo_anomaly_gate_off.scala) —
with an empty validated-field list, the model generated a rule that
`.filter(lit(false))`s everything, i.e. **a detector that compiles clean,
deploys clean, and never fires, silently, forever.** That's arguably worse
than a false positive: nothing about it looks broken from the outside.
The gate turns that into an explicit, visible rejection instead.

## Calibration: cross-extractor agreement as a real confidence proxy

No system in this project emits a self-reported confidence score — building
one and calibrating it would have been new scope, not a missing
measurement, and LLM self-reported confidences are notoriously poorly
calibrated anyway. Instead, `calibration_analysis.py` uses a real,
already-computed signal: agreement between the two independently-implemented
Phase 2 extractors (classical vs. transformer) on the required-field set.
Result: [`phase5_calibration_check.json`](phase5_calibration_check.json).

| Report | Agreement (Jaccard) | Transformer F1 |
|---|---|---|
| SF-SAMPLE-013 | 1.000 | 1.000 |
| SF-SAMPLE-011 | 1.000 | 1.000 |
| SF-SAMPLE-012 | 1.000 | 1.000 |
| SF-SAMPLE-014 | 0.750 | 0.857 |
| SF-SAMPLE-015 | 0.500 | 0.857 |

Pearson r = **0.919** (n=5, stated plainly as a small sample, not claimed
as general significance). The simple decision rule this suggests —
*disagreement between the two extractors → flag for mandatory analyst
review* — would have flagged exactly the 2 reports with F1 < 1.0 and
none of the 3 perfect ones, in this data.

## Adversarial test set, broadened beyond prompt injection

Phase 6 built 3 prompt-injection fixtures (`nlp/test_injection_guard.py`).
This pass adds 3 more, targeting extraction robustness rather than the
model's compliance — real results in
[`phase5_adversarial_extraction.json`](phase5_adversarial_extraction.json):

- **Contradictory threshold** (report states 5, then explicitly corrects
  to 10): both extractors correctly resolved to the corrected value (10),
  not the earlier draft figure — a real, clean pass for both.
- **Alarming tone over compliant facts** ("CRITICAL SECURITY INCIDENT"
  framing a fully MFA-compliant login): the transformer correctly read
  through the tone to `mfa-bypass-on-required-account`; classical
  misclassified it as `service-account-interactive-auth` — a real,
  measured failure the keyword-matching approach has that semantic
  extraction doesn't.
- **Synonym substitution** (the B1 pattern described with "identity"
  instead of "account," "credential rejection" instead of "failed login,"
  "session established" instead of "successful login"): classical's
  extraction **collapsed to a single field** (`timestamp` only — it lost
  `account_id` and `event_type` entirely, along with the threshold and
  window). The transformer extracted the full, correct spec unaffected.
  This is the clearest, most dramatic real evidence in this project for
  why keyword-based extraction alone isn't sufficient in production.

## Throughput / latency (honestly scoped)

`ThroughputCheck.scala` — 10 repeated runs per behaviour against the real
48-event dataset, `local[*]` on this machine. Real result:
[`phase5_throughput_check.json`](phase5_throughput_check.json). Mean
503ms, p50 437ms, p95 1136ms per query (~95 events/sec at this scale) —
dominated by Spark's per-query planning overhead on a dataset this small,
stated explicitly rather than presented as a production-scale number. A
real measurement of what exists, not a benchmark claim the data can't
support.

## What's honestly still missing

- **Failure recovery at scale** — the missing-field robustness test is one
  real data point; nothing systematic was measured under sustained load
  or partial-cluster failure (this dataset and single-machine setup
  couldn't support that kind of test meaningfully anyway).
- A **larger held-out set** for the calibration and extraction-F1 numbers
  — n=5 is real but small; the finding (agreement correlates with
  correctness) is worth re-checking as report volume grows.
