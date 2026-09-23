> **See [`docs/spec/independent-review-corrections.md`](../../docs/spec/independent-review-corrections.md)**
> for a significant correction: an independent review found that every
> result in this file originally ran against hand-authored compiled
> specs, not ones produced by actual extraction — i.e. these numbers
> proved the compiler correct, not the full report-to-detection path. A
> real bridge now exists and has been run end-to-end for real; see that
> file for what was found and fixed.

# Phase B — compiler vs labels at scale

`phaseB_generated_dataset_check.json` (written by `GeneratedDataCheck.scala`): the real Spark `RuleCompiler`
over **36,684,108 events (1.055 GB Parquet)** with **10,900** independently constructed labels — **10,900 / 10,900
satisfied, 0 unexpected, 0 missing alerts**, about 97 s on one machine. Synthetic background; read it with the
caveats in [`docs/data-sources.md`](../../docs/data-sources.md#labels-vs-the-real-compiler-at-scale-2026-09-22).
The generated dataset itself is not committed (regenerate with the seed; hashes are in that doc).

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

## Calibration: cross-extractor agreement and self-reported confidence, re-run at n=44

**Updated since the first pass — the n=5 finding below did not survive a larger, more relevant
sample, and that's reported honestly rather than left standing.** The original version of this
section ran on 5 held-out reports (the only held-out set that existed before the v2 corpus) and
found Pearson r = 0.919 between classical/transformer agreement and F1 — but "transformer" there
meant the Phase 2 prompted LLM, before a fine-tuned model existed. `docs/phase-c-extraction.md`'s
44-report test split (never touched during Phase C's training or model selection) is now the
larger held-out set this section's own "What's honestly still missing" called for, and
`calibration_analysis.py` was rewritten to use it — see that file's module docstring for the full
account of what changed and why. Result:
[`phase5_calibration_check.json`](phase5_calibration_check.json).

**The disagreement rule does not replicate at scale.** Classical is near-universally in
disagreement with the fine-tuned model (Phase C's ablation already found classical "far too weak
on this corpus to usefully vote on anything" — this is that same weakness, now visible as a
calibration signal): Pearson r (agreement vs. fine-tuned F1) = **0.099**, and "disagreement → flag
for review" flags 44/44 reports, i.e. everything — precision 0.295, exactly the base rate of
imperfect extractions in this split. A rule that flags every report is not a useful rule.

**What actually works: the fine-tuned model's own confidence.** `finetuned_extractor.py` now
returns a real softmax confidence per prediction — unavailable at n=5, when this section was
written. Pearson r (confidence vs. fine-tuned F1) = **0.767**. Gating on
`hybrid_extractor.DEFAULT_CONFIDENCE_THRESHOLD` (0.6) — the same threshold the hybrid design
already ships — flags 5/44 reports at **precision 1.0** (every flagged report really was
imperfect) but **recall 0.385** (it misses 8 of 13 imperfect reports that stayed above threshold
anyway). That precision/recall tradeoff, not a single clean number, is the honest, actionable
result: confidence-gating is a precise but not very sensitive review trigger, which is exactly the
role it already plays in `hybrid_extractor.py`, not a new, independent finding.

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

- ~~Failure recovery at scale~~ — **closed in Phase E**:
  `StreamingRecoveryCheck.scala` runs a real `StreamingQuery` kill and
  restart against real checkpoint recovery
  (`docs/phase-e-scale.md`, item 17, PASS). Real cluster / multi-node
  failure is a separate, still-open item there (item 16), blocked on
  cloud account access this environment doesn't have — not a Phase 5 gap.
- ~~A larger held-out set for calibration~~ — **closed above**:
  re-run at n=44 (the corpus's real test split), see the calibration
  section. The finding changed, not just the sample size: cross-extractor
  agreement doesn't hold up at scale, but the fine-tuned model's own
  confidence does — a real update, not a confirmation of the n=5 result.
  Extraction F1 itself was already re-measured at n=44 independently in
  `docs/phase-c-extraction.md`'s main comparison, with bootstrap CIs.
