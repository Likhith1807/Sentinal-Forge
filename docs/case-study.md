# Case study: teaching a detection compiler to say "I don't know"

*SENTINEL Forge — turning threat-report prose into Spark detections, and what an audit of my own first version taught me.*

## The problem

Threat reports are prose. Detection engineers turn them into rules by hand: read "more than five failed logins in two minutes
followed by a success", write a query, hope they read it right. It is an obvious job for a language model — and an obvious way to
ship a wrong detection with a confident-looking rule attached.

I built a compiler from report text to Spark detections for five authentication behaviours (brute-force-then-success, password
spray, multi-host authentication, auth-method mismatch against policy, missing MFA), and the first version worked in a demo. Then I
audited it against its own test data instead of trusting it.

## What the audit found

Run honestly, my first pipeline compiled **27 of 40** supported reports correctly. Worse than the 13 misses were **7 silent
failures** — cases where it produced a rule and said nothing:

* *"90 seconds"* became a **90-minute** window. The unit was rewritten on the way through, and nothing checked the text.
* An ambiguous count ("logins") was read as distinct accounts. A different behaviour entirely.
* A port-scan report was accepted as a supported behaviour because a classifier score was high.

None of these were model failures in a benchmark sense; each would have produced a running detection that was *wrong* in
production, with a green tick beside it. The right response was not a better model. It was changing what a model is allowed to do.

## The design decision: propose, then prove

The system now treats a model — if one is used at all — as a *proposer*. A separate, deterministic **condition finder** reads the
report and records every candidate number, unit, field mention and qualifier **with its exact quote and character offsets**. A
**reconciler** compares the proposal with the evidence and returns exactly one of three outcomes:

* **accepted** — every value is backed by a quote and nothing contradicts it;
* **rejected** — positively incompatible (an unsupported behaviour, a qualifier no recipe can evaluate);
* **needs review** — evidence is absent, ambiguous or contradictory (two windows, a correction, a hedge like "possibly 15").

"90 seconds" cannot become 90 minutes because the text has to say "minutes". A model can never make a number acceptable that the
passage does not contain. Then a **closed recipe compiler** — three fixed shapes, validated numbers only, no free-text SQL —
means a hostile report cannot inject a predicate, only a value inside a schema.

## Making "correct" testable: two engines and a mutation check

"The rule is right" needs a definition. I wrote the detection semantics down (closed window `[t−W, t]`, microsecond timestamps,
quarantine for malformed rows, dedupe by `event_id`, exact distinct counts, rising-edge alerts, versioned policy) and then implemented
them **twice** — the Spark engine, and a small, deliberately naive Python reference engine written independently. Generated scenarios,
golden cases and metamorphic properties ("redelivering an event changes no alert") compare them.

Then I asked the uncomfortable question: *would this comparison notice a bug?* A mutation check plants seven realistic defects
(off-by-one on the window boundary, dropped dedupe, approximate distinct counts, …). The harness caught all seven. When I fixed the
batch engine's edge cases, I extended the same semantics to Structured Streaming, and compared batch and streaming under an explicit
lateness policy: 8 regimes, 192 scenario runs, 0 disagreements; hard-killing the JVM at three points and restarting from the checkpoint lost
and duplicated nothing. Along the way real bugs surfaced only because both engines existed — a Windows launcher stub that left orphaned JVMs
running after the parent was killed, a Spark optimiser rewriting my "heartbeat" row out of the plan, a decimal overflow that turned a
window start into null.

## Measuring the language part without fooling myself

The first evaluation set was the 44 reports I developed against — worthless as evidence once I had fixed things against it. So:

* I **froze** a holdout before running anything (SHA-256 for every file, a git tag), wrote 112 reports in voices the training corpus
  never used, including 16 verbatim passages from CISA/FBI advisories, ran every system **once**, and published the failures.
* A blind LLM from a different vendor labelled it independently (kappa 0.886; six disagreements adjudicated) — and I labelled that
  clearly as *model-assisted*, because a human second reviewer is still outstanding.
* The result was humbling and useful: the raw fine-tuned model compiled a wrong rule **silently** for 32% of supported reports and
  accepted 56% of the reports that must be refused. With the evidence check, silent errors went to **0**, at the price of refusing
  more than half. And a model in the loop *lowered* recall against the evidence finder alone — so the default extractor is no model.
* Those failures fell into phrasing *classes*; I generalised the parser for the classes (not the sentences), then wrote a **second,
  fresh holdout** for the fixes: recall went from 44% to 63% on new wording, silent errors stayed at 0 — and five must-refuse reports
  were wrongly accepted ("no need to alert when…", "decided not to build it", "possibly 15"). I published those, fixed them with
  regression tests in *new* wording, and recorded plainly that the fixed number is no longer held-out. The freeze even caught me:
  a stale report survived my first cut of the holdout, so I re-cut it and archived the aborted run.

I did not finish everything I wanted: the prompted-LLM comparison is partial (the provider's daily token quota ended it twice, and the
UI says so), there is no human second reviewer, and no practising detection engineer has looked at the tool. Those are in the
evaluation document as open, not omitted.

## From a demo to a workflow

The service layer makes the compiler something an analyst can use: paste a report → inspect each condition beside its quote →
check it against the actual dataset's schema → compile a versioned rule → run it as a background job against a dataset version →
inspect matching events and per-alert evidence records → approve exactly that rule version. Everything is persisted (SQLite WAL,
compare-and-set approvals, UUID-isolated atomic run directories). Its most useful feature is the boring one: *schema-change impact
analysis.* Remove `source_host` from the collector and the system pauses the spray rule, leaves the brute-force rule running, names the
blocked condition, and refuses to resume until the rule is revalidated — before production, not after.

## Performance, honestly

On one 10-core laptop with a local Spark session, the batch engine evaluates all five behaviours over **36.7M events** and matches
**10,900 of 10,900** labelled incidents exactly; `docs/benchmarks.md` gives the timings with hardware, heap, worker count, dataset
shape, sample sizes and failure rate, cold versus warm, and streaming latency separately. It makes **no distributed claim**: nothing
was measured on more than one machine.

## What I would do next

1. A third holdout, to measure the post-v2 fixes honestly; a human second reviewer for both.
2. Real logs (a data request to LANL is drafted) — the compiler is exactly right on synthetic data, which is a weaker statement than it sounds.
3. A practitioner review: does a detection engineer trust the evidence panel, and what do they want in it?
4. A distributed benchmark before any scale claim.

## What this project is meant to show

Not that a model can read a threat report — plenty of demos do. That a system can be built around a model's *failure modes*:
refuse when unsure, make every claim traceable to a quote, test the tests, freeze the evaluation before looking, and publish what
went wrong.
