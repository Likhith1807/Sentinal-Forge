# Baselines

The three weaker systems SENTINEL Forge is benchmarked against in Phase 5
(see [`docs/examples/phase0-canonical-example.md`](../../docs/examples/phase0-canonical-example.md)
and Figure 2 of the [pipeline diagram](https://claude.ai/artifact/M5b9qejRfECwHHXFu9PFX7)).

| System | Where | Status |
|---|---|---|
| Manual rules | [`manual/`](manual) | **Done** — hand-authored Scala for all 5 behaviours (B1 delegates to the Phase 0 golden reference; see that file's header for why). |
| Direct LLM generation | [`direct_llm/`](direct_llm) | **Done** — run against Groq (`openai/gpt-oss-120b`) for all 5 behaviours. See `direct_llm/README.md` for real findings from the run, including a confirmed insufficient-context bug in the generated B4/B5 rules. |
| Schema-constrained generation | [`schema_constrained/`](schema_constrained) | **Done** — extract → Stage 3 validate → generate-only-if-supported, run for all 5 behaviours plus a deliberate rejection-path test. See `schema_constrained/README.md`: the rejection path works end-to-end, and a real generated rule fabricated a hardcoded one-account allowlist when denied the policy field it needed. |
| SENTINEL Forge (full) | *(Phase 2–4)* | Not started — needs evidence-linked generation on top of what schema-constrained already does. |

## Why manual rules exist for all five behaviours already, but the others don't

The manual baseline needs nothing but a person reading the report — which
is exactly what building `manual/` by hand *was*. The other three systems
need pipeline components (an LLM call with credentials, Stage 3's checker,
the NLP extractor) that don't exist yet. Building the manual baseline first
was not arbitrary: every later system's output on B1–B5 gets compared
against these five files, so getting them right first gives every later
comparison a stable, human-verified floor.

## A known gap in the manual baseline, left visible on purpose

`ServiceAccountInteractiveAuth.scala` and `MfaBypassOnRequiredAccount.scala`
correctly emit `insufficient_context` for an account missing from the
policy reference (see their headers). That correctness is not a given for
the other three systems — direct LLM generation in particular is expected
to struggle here, since nothing in its prompt (`direct_llm/prompt_template.md`)
tells it that a missing join key is a distinct outcome from "no alert." This
is one of the concrete things Phase 5's `insufficient_context` handling
comparison is meant to measure.
