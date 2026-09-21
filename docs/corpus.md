# Report Corpus v2 (`data/corpus/`)

The 15-report v1 set ([`data-splits.md`](data-splits.md)) is a correctness harness: one template, too small
for a meaningful F1. This corpus is the larger, family-split, leakage-checked set that training and
evaluation numbers must come from. **Read the limits section before quoting any number from it.**

## What it is

A **synthetic** corpus. Every report is rendered from a structured *fact sheet*, so its gold labels are exact by
construction and every gold value carries a character-offset span, recorded as the text is written (so an offset
cannot disagree with its text; asserted at build time for every span in every report).

| | |
|---|---|
| Families | 110 = 5 in-scope behaviours x 20 + 10 unsupported (abstention) families |
| Tiers | `template` (rendered directly) and `llm-rewrite` (the same incident restyled by an LLM under guards) |
| Files | `reports/SFC-*.md`, `gold/SFC-*.gold.json`, `splits.json`, `leakage.json`, `manifest.json`, `llm_cache.jsonl`, `verification/annotation_template.jsonl` |
| Rebuild | `python -m scripts.corpus.build --out data/corpus` (offline with `--skip-llm`; a rebuild reads `llm_cache.jsonl`, so it is reproducible without calling the API) |

### Gold format (compatible with `data/samples/ir/gold/` plus extras)

`behaviourId`, `requiredFields`, `policyFields`, `excludedFields`, `threshold` (`{failureCount|distinctAccountCount|successCount: N}`),
`timeWindow` (`{amount, unit}`) as in v1; plus `spans` (label, text, start, end, and for values the normalised
`value`), `familyId`, `style`, `tier`, `fieldMode`, `numMode`, `thresholdForm`, `windowForm`, `hasParamSection`,
and `supported`. For unsupported reports `behaviourId` is `null`, `supported` is `false`, and `unavailableFields`
lists what the report needs that the schema lacks.

## Where diversity comes from

- **Four structural styles** (advisory with bullets, SOC ticket, post-mortem prose, informal handoff message) and,
  in the LLM tier, five more (email, intel brief, incident review, chat message, runbook).
- **Field mentions** are backticked identifiers in about half the reports and natural-language phrases
  ("the sign-in outcome", "whether a second factor was used") in the rest, so an extractor must map language to
  schema fields, not just spot names.
- **Numerals** are digits or words; **thresholds** are phrased five ways ("at least 5", "5 or more", "5+",
  "no fewer than 5", "more than 4" — where the gold is 5, a deliberate normalisation trap); **windows** are spaced or
  hyphenated ("2 minutes", "2-minute"), from 30 seconds to 1 hour.
- **Distractor numbers** (observed counts and durations that differ from the rule's threshold and window, ticket
  ids, times, subnets) and **incidental fields** the report explicitly says are not part of the pattern.
- **Only a minority of reports use the v1 "Analyst-confirmed detection parameters" heading** that the classical
  extractor exploits; the rest state the rule in prose.
- **Ten unsupported families** (DNS tunnelling, exfiltration volume, encoded PowerShell, impossible travel,
  ransomware renames, phishing click, privilege escalation, port scan, lockout storm, USB storage). Several
  deliberately resemble an in-scope behaviour but need a field the schema lacks (impossible travel needs
  `geo_country`, a lockout storm needs a lockout event). A system that maps them to an in-scope behaviour has
  overreached. Their text does **not** announce that they are unsupported (a test enforces this; an earlier
  version leaked it through the ATT&CK line and was caught and fixed).

## How the LLM tier stays honest

The template report is the source of truth. A rewrite is accepted only if:

1. every gold-bearing phrase survives **verbatim**: the whole threshold expression ("at least 5"), the whole window
   expression ("2 minutes") and every required-field mention, so each span is relocated by exact search;
2. it adds **no digit sequence** the original lacked (numbered-list markers excepted);
3. it adds **no new hostname or ticket-like identifier**;
4. for unsupported reports, it does not announce that the behaviour is unsupported; and
5. its length is sane.

Two things are tolerated on purpose, because no gold depends on them: dropping a hostname, ticket id or analyst
name, and dropping the "this field is incidental" note (the report's `excludedFields` is then reduced to match).
Both were measured to be the main causes of rejecting otherwise faithful rewrites.

**What these guards do not guarantee:** that the prose *around* the preserved phrases means the same thing. An
LLM could, in principle, keep "at least 5" verbatim and still change what is being counted. That risk is why the
tier is labelled, why the human verification sample below is stratified across both tiers, and why the LLM tier
must not be treated as human-verified.

## Splits and leakage

The split is by **family** (a family's template and rewrite are near-duplicates by construction), stratified by
behaviour, 60/20/20, seeded. A gate fails the build if any cross-split report pair has raw 5-gram Jaccard >= 0.5.
The leakage report also measures **template similarity** (numbers, identifiers and hostnames masked), which is the
risk that a model passes the test split by recognising report skeletons instead of extracting; it is reported, not
gated, because a template-driven corpus is expected to have some. See `leakage.json`.

## Measured results (built 2026-09-22, seed 42)

| | |
|---|---|
| Reports | **201** = 110 template + 91 LLM-rewrite, from 110 families |
| Split (by family) | train 118, dev 39, test 44; every behaviour has 8 test reports (4 families), unsupported has 4 |
| Per behaviour | B1 39, B2 39, B3 38, B4 39, B5 30, unsupported 16 |
| LLM tier | 91 of 110 families have a rewrite. Of the 92 attempts the API answered, **91 were accepted (about 99%)**, 85 of them on the first try and 1 rejected by a guard |
| Families without an LLM variant | **19** (ids in `manifest.json`): 18 not attempted after the daily token quota ran out, 1 guard rejection. B5 is the worst hit (10 of its 20 families have only a template report) |
| Leakage gate | passed: 0 families straddle splits, 0 exact duplicate texts, max cross-split raw 5-gram Jaccard **0.296** (gate 0.5) |

**Template similarity** (the skeleton-recognition risk; cross-split pairs about the same behaviour, numbers/identifiers
masked; `leakage.json`):

| pair tiers | pairs | mean | p95 | max |
|---|---|---|---|---|
| template / template | 588 | 0.093 | 0.232 | **0.466** |
| llm-rewrite / template | 1,004 | 0.013 | 0.051 | 0.169 |
| llm-rewrite / llm-rewrite | 441 | 0.010 | 0.036 | 0.080 |

The template tier shares real skeleton with itself (max 0.466 is close to the raw gate value, though this measure is
not gated); the LLM tier is roughly an order of magnitude less similar to anything. So **a model evaluated only on
template-tier reports could be partly rewarded for recognising skeletons**; report the tiers separately.

**Difficulty check** (`experiments/results/phaseB_corpus_classical_difficulty.json`, test split, 40 supported-behaviour
reports): the Phase 2 classical extractor scores field micro-F1 **0.05** and behaviour accuracy 0.23, against 0.97
and 0.80 on the five v1 reports. That is because it only reads the old "Analyst-confirmed detection parameters"
section (the one report here with such a section scored F1 0.67). It shows the corpus no longer rewards that
shortcut; it does **not** show the corpus is fair or solvable.

## Limits (state these whenever a corpus number is quoted)

- **No independent reader has yet confirmed the gold.** A probe that runs the LLM extractor on a sample of test
  reports and prints every disagreement (`python -m scripts.corpus.probe_llm`) was attempted and scored **0 of 16**,
  because the provider's daily token quota (200,000) was already spent by the LLM tier build. It has not been run.
  Until it (or a human pass) is, gold correctness rests on construction and on the Stage 3 / bridge integration test
  (every supported gold validates as `supported` and compiles to the gold numbers), not on an independent reader.
- **The LLM tier used two reasoning settings**: 75 rewrites at the provider default and 16 at `low` (switched
  mid-build to cut token cost); recorded per report as `llmReasoningEffort`.
- **Test split is small** (44 reports, 40 supported): enough to catch failures, not to put tight confidence
  intervals on an F1. Phase C needs the larger sample or repeated runs.
- **Synthetic.** Phrasing is drawn from pools written by the project and by one LLM. Performance here does not
  transfer to real threat reports; it says whether a system can extract *this* structure. A small real-report
  subset (public advisories) is still to be collected and would need its own annotation.
- **Gold is exact for the template tier by construction, but not human-checked.** The LLM tier's gold is inherited
  from its template, not independently annotated.
- **No inter-annotator agreement exists yet.** The blind sheet and scorer are built
  (`python -m scripts.corpus.agreement sample|score`), but no human has annotated the sheet. Until one does, any
  agreement figure would be invented, so none is reported. Two independent annotators are needed for a real
  kappa; the scorer takes one annotator against gold, so a second annotator's sheet can be scored the same way.
- **Only the five behaviours and the current schema.** Threshold/window values are the ones the fact sampler
  draws; the replay dataset uses the compiled specs' fixed parameters, so extraction-to-replay at these varied
  parameters is a later check.
- **Extractor difficulty check** is on the supported-behaviour reports of one split; it is a sanity check on the
  corpus, not an evaluation of the project's extractors (that is Phase C).
