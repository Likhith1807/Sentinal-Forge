# Architecture

SENTINEL Forge turns a paragraph of threat-report prose into a *reviewed, versioned, evidence-linked detection rule*, runs it on
data, and lets an analyst approve it. The design decision that shapes everything: **a language model may propose, but only evidence
in the passage can authorise compilation.**

```
                  ┌───────────────────────────── understanding ─────────────────────────────┐
 report text ───▶ │ conditions.py   deterministic finder: counts, windows, fields, qualifiers,│
 (pasted)         │                 behaviour cues — each with a character-offset quote       │
                  │ extractors      optional proposer (fine-tuned model / prompted LLM)       │
                  │ reconcile.py    proposal + evidence ─▶ accepted | needs_review | rejected │
                  └──────────────────────────────────┬──────────────────────────────────────┘
                                                     │ every outcome carries reason codes + evidence
                  ┌──────────────── validation ──────▼──────────────────────────────────────┐
                  │ validation.py   closed schema: behaviour registry, field/type/limit       │
                  │                 checks; unknown, malformed, ambiguous ⇒ refusal           │
                  │ schema_impact   can THIS dataset's schema observe every required field?   │
                  └──────────────────────────────────┬──────────────────────────────────────┘
                  ┌──────────────── compilation ─────▼──────────────────────────────────────┐
                  │ compile.py      typed spec + rule hash (no free-text SQL, no codegen)     │
                  │ RuleCompiler.scala / refengine.py   same semantics, two implementations   │
                  └───────────────┬──────────────────────────────┬───────────────────────────┘
                     batch (Spark)│                              │streaming (Structured Streaming)
                  ┌───────────────▼──────────┐        ┌──────────▼──────────────────────────┐
                  │ RunRule.scala            │        │ StreamingEngine.scala               │
                  │ exact distinct counts,   │        │ per-key state, lateness policy,     │
                  │ µs timestamps, quarantine│        │ duplicates, expiry, policy re-read  │
                  └───────────────┬──────────┘        └──────────┬──────────────────────────┘
                                  └──────────── alerts + evidence ┘
                  ┌──────────────── service (SQLite WAL) ────────────────────────────────────┐
                  │ reports · analyses · rule versions · runs · alerts · evidence · datasets  │
                  │ job states persisted; approvals compare-and-set against a rule version    │
                  └──────────────────────────────────┬──────────────────────────────────────┘
                                          FastAPI + five-screen UI
```

## The single source of truth: the behaviour registry

`sentinelforge/behaviours.py` defines the five behaviours once — recipe, event type, log fields, policy fields, count semantics,
accepted threshold aliases, window limits. The validator, the compiler, the extractors' prompts, the UI and the tests all read
from it. Adding a behaviour is a registry entry plus its test scenarios; there is no second list to forget.

| behaviour | recipe | counts |
|---|---|---|
| repeated failed login, then success | SequenceThenTrigger | failed **events** per account |
| password spray across accounts | DistinctCountWithinWindow | **distinct accounts** per source host |
| multi-host authentication | DistinctCountWithinWindow | **distinct hosts** per account |
| auth method ≠ policy | PolicyCompare | per successful login |
| MFA missing where required | PolicyCompare | per successful login |

The old name for the third behaviour ("impossible travel") over-claimed. It is a *multi-host* rule: the schema has no location,
so it cannot know that travel was impossible, and now it does not say so.

## Understanding: propose, then prove

* **`conditions.py`** finds every candidate number, window, field mention and qualifier in the passage and records the exact quote
  and offsets of each. It makes *no decision*.
* **`reconcile.py`** decides. It compares the model's proposal (if any) with the candidates and returns one of three outcomes:
  *accepted* (each value is backed by a quote and nothing contradicts it), *rejected* (positively incompatible: an unsupported
  behaviour, a qualifier no recipe can evaluate), or *needs_review* (evidence absent, ambiguous or contradictory — e.g. two windows,
  a correction, a hedge, "logins" with no count semantics). A proposal can never make a value acceptable that the passage does not
  contain: 90 seconds cannot become 90 minutes, because "minutes" has to be written in the text.
* The proposer is pluggable. The default is *no model* (the deterministic finder alone) because the frozen holdouts showed it
  compiled at least as many reports correctly as any model-assisted path with the same zero silent errors
  (`docs/evaluation.md`). The fine-tuned RoBERTa and a prompted LLM remain selectable, and each is bounded by the same evidence check.

## Validation and compilation

`validation.py` rejects, with a reason each: unknown behaviours, booleans passed as numbers, NaN and infinities, thresholds outside the
recipe's range, malformed blocks, conflicting keys (two threshold aliases with different values), missing or extra fields, and any
field the dataset's schema cannot observe (`schema_impact.py`). Every field that is read is checked; every field that is emitted is
defined.

`compile.py` produces a typed, hashed spec. There is no string-built SQL from report text: the Scala `RuleCompiler` fills one of three fixed
shapes with validated numbers, so a hostile report cannot inject a predicate.

## Two engines, one semantics

`docs/spec/detection-semantics.md` (v2) is the contract; the Spark engine and an independent, deliberately simple Python reference
engine (`refengine.py`) both implement it and are compared on generated scenarios, golden cases and metamorphic properties
(redelivery changes no alert; shifting all timestamps changes none; …). A mutation check plants seven realistic bugs and confirms
the comparison catches all seven. Streaming is a third implementation of the same semantics and is compared with batch under an
explicit lateness policy.

## The service

SQLite in WAL mode, every state change in a `BEGIN IMMEDIATE` transaction, approvals by compare-and-set on the rule version:

* **Reports** are immutable text. An **analysis** ties a report, an extractor and its output together and moves through persisted
  states (`queued → extracting → validating → ready | needs_review | rejected | failed`) that survive a restart.
* A **rule version** (`draft → approved → paused → superseded → retired`) is bound to one analysis and one rule hash. Refining a rule
  rebuilds it server-side from edited *values*, then re-validates; the client never posts a rule.
* A **run** ties a rule version to a dataset version and produces alerts with evidence records (six questions:
  *why* — the quote-verified passage; *means* — the normalised conditions; *supported* — which fields and policy the data provides; *fired* — the
  supporting events; *executed* — rule version, hash, engine, run id; *uncertain* — what is not known or not verified). It is an evidence record, not a proof. A UUID isolates each run
  directory; files are written atomically.
* **Dataset versions** record their schema. Removing `source_host` is a *schema change*: the impact analysis pauses every rule that
  needs it, leaves the unaffected ones running, and requires revalidation before resume.
* **Approval** references the server's rule version, so an analyst approves *exactly* what ran, not what a browser remembers.

## Streaming, in one paragraph

Windowed recipes keep a per-key buffer, finalised in timestamp order once an event `lateness` newer has arrived (or a heartbeat
advances the watermark); late and duplicate events are emitted as records, not dropped; state expires when the global watermark
passes `newest + lateness` and `expiry ≥ window + lateness` guarantees expiry cannot change an alert. Policy recipes are stateless and
re-read the policy table every micro-batch, applying versioned rows at each event's own timestamp. Output is one file per kind per
batch id, written atomically, so a replayed batch overwrites itself: *effectively-once under conditions*, documented in `docs/limitations.md`.

## Security posture

Local-demo mode binds to loopback only and has no login. Any other bind requires token mode: bearer tokens compared in constant time, with roles
(`analyst` can create, run, approve and change schemas; `viewer` can only read), a rate limit, request size limits, a Content-Security-Policy and generic
500 responses (no stack traces). The identity recorded on a decision comes from the token, never from the client. Report text is treated as
untrusted throughout — it can only ever influence *values inside a closed schema*.

## Where things are

| path | contents |
|---|---|
| `sentinelforge/` | behaviours, conditions, reconcile, validation, compile, reference engine, Sigma, Spark/streaming launchers |
| `sentinelforge/service/` | store, workflow, jobs, datasets, schema impact, evidence, extractors |
| `compiler/` | Scala: `RuleCompiler`, `RunRule`, `StreamingEngine`, checks and benchmark harness |
| `dashboard/` | FastAPI backend + dependency-free five-screen frontend |
| `nlp/` | corpus tooling, the classical / fine-tuned / prompted extractors |
| `data/holdout*/` | the frozen holdouts, hashes, reviews |
| `experiments/` | audit, holdout evaluation, archived results |
| `scripts/` | verification, benchmark, packaging, data generation |
