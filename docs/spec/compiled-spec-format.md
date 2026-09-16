# Compiled Spec Format (v1) — Stage 4's actual input

The full typed IR in
[`behaviour-ir-format.md`](behaviour-ir-format.md) is the conceptual
format Stage 2/3 reason about (entities, provenance, arbitrary predicates).
Stage 4's real compiler (`compiler/src/main/scala/.../RuleCompiler.scala`)
consumes a narrower, flat, closed format instead: every behaviour compiled
so far reduces to exactly one of 3 parameterized **recipes**, and the
compiler is a Scala `match` over those 3 cases — nothing more general than
that. This is a deliberate design choice, not a limitation being worked
around: a closed recipe set is what makes the compiler's output space
*restricted*, matching the README's "restricted Scala/Spark rule" language.
It cannot generate code an LLM might invent; it can only ever produce one
of 3 well-understood query shapes.

## The 3 recipes

**`SequenceThenTrigger`** (B1) — count `countEventType` events for the same
`groupingKey` in the trailing `timeWindowSeconds` before a `triggerEventType`
event; alert if the count is `>= countThreshold`.

**`DistinctCountWithinWindow`** (B2, B3) — count *distinct* values of
`distinctField` among `filterEventType` events for the same `groupingKey`
within `timeWindowSeconds`; alert if `>= distinctThreshold`. B2 and B3 are
the same recipe with grouping/distinct swapped: B2 groups by `source_host`
and counts distinct `account_id`; B3 groups by `account_id` and counts
distinct `source_host`.

**`PolicyCompare`** (B4, B5) — no time window at all: left-join
`filterEventType` events against the policy reference on `account_id`, and
compare `logField` to `policyField` per `comparisonOp`
(`notEqual` for B4, `falseWhenRequired` for B5). Emits `status` ∈
{`alert`, `no_alert`, `insufficient_context`} — the same three-way outcome
the manual baseline established in Phase 1, now implemented once, generically,
instead of by hand per behaviour.

## Files

- `data/samples/ir/compiled/*.compiled.json` — one flat, single-line JSON
  object per behaviour, in this format.
- `compiler/src/main/scala/sentinelforge/compiler/CompiledSpec.scala` —
  the case class and loader (reads via `spark.read.json`, not a new JSON
  dependency — the compiler is a Spark program already).
- `compiler/src/main/scala/sentinelforge/compiler/RuleCompiler.scala` —
  the 3-recipe interpreter.

## What generalizing this later actually means

Every current behaviour fits one of 3 recipes because all 5 were designed
around count/distinct/compare patterns over one schema. A 6th behaviour
needing a genuinely new shape (say, a numeric-range predicate, or a
3-step sequence) would need a 4th recipe added deliberately — not a
generic expression evaluator bolted on. Keeping the compiler closed like
this is what makes Stage 3's validation meaningful: Stage 3 can reason
about "does this recipe's required fields exist" precisely because the
recipe set itself is small and fixed.
