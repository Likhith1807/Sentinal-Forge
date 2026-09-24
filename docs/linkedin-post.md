# LinkedIn post (draft)

Edit the bracketed parts (repo link, demo link). Every figure below is backed by a file in the repo; the source is in brackets so you can
check it before posting. If a number changes, change the post.

---

**I audited my own AI project and found it was silently wrong 1 time in 6. Here's what I built instead.**

SENTINEL Forge turns a paragraph of threat-report text into a Spark detection rule — for five authentication behaviours.

My first version looked great in a demo. Then I ran it against its own test data:

→ "90 seconds" compiled into a 90-**minute** window
→ an ambiguous count was read as a different behaviour
→ a port-scan report was accepted as something it wasn't
→ 27 of 40 correct, 7 wrong-with-a-green-tick

So I stopped improving the model and changed what the model is *allowed to do*:

🔹 A model may only **propose**. Every number, unit and field must be backed by an exact quote from the report — or the report goes to human review. "90 seconds" can't become 90 minutes, because the text has to say "minutes".
🔹 Two independent engines (Spark and a small Python reference) must agree on the semantics — and a mutation test confirms the comparison catches 7 of 7 planted bugs.
🔹 A batch↔streaming agreement suite, including hard-killing the JVM mid-run: no lost or duplicated alerts.
🔹 A **frozen** evaluation: I hashed and tagged 112 unseen reports (16 verbatim CISA/FBI advisory passages) *before* running anything, ran once, and published the failures.

What the frozen test showed:
• the raw fine-tuned model was silently wrong on **32%** of supported reports and accepted **56%** of reports it should refuse
• with the evidence check: **0%** silent errors — at the cost of refusing more than half
• a model in the loop *lowered* recall, so the default extractor is now no model at all

I fixed the parser for those classes of failure, wrote a fresh holdout, and recall went from 44% → 63% with silent errors still at 0. It also wrongly accepted 5 reports it should have refused — published, fixed with regression tests, and marked as no-longer-held-out.

On one laptop: 36.7M events, 10,900 / 10,900 labelled incidents matched exactly. No "distributed" claim — I only measured one machine, and the repo says so.

What's *not* done, stated in the repo: no human second reviewer, no practitioner feedback, the prompted-LLM comparison is partial (API quota), all data is synthetic.

If you build with LLMs: the useful question isn't "how accurate is it?" — it's "what does it do when it's wrong, and can I see why?"

Repo + case study: [link]   ·   3-minute demo: [link]

#DetectionEngineering #ApacheSpark #Scala #MLOps #AppliedAI #CyberSecurity

---

### Numbers and where they come from

| claim in the post | source |
|---|---|
| 27 of 40, 7 silent failures | `experiments/results/audit_pipeline_baseline.json` (`counts`: 5 wrong rules + 2 silently accepted) |
| 7 of 7 planted bugs caught | `experiments/results/differential_mutation_check.json` |
| no lost/duplicated alert after kill | `experiments/results/streaming_recovery.json` (3 kill points) |
| 112 reports, 16 CISA/FBI passages, frozen and tagged | `data/holdout/FROZEN.json`, tag `holdout-v1-frozen` |
| 32% / 56% raw model; 0% with evidence check | `experiments/results/holdout/summary.json` (`finetuned-raw`, `finetuned+evidence`) |
| 44% → 63%, 5 false accepts | `experiments/results/holdout/summary.json`, `holdout_v2/summary.json`, `holdout_v2/POST_HOC.md` |
| 36.7M events, 10,900 / 10,900 | `experiments/results/phaseB_generated_dataset_check.json` |

"1 time in 6" = 7 silent failures out of 44 reports (16%) in the original audit split. If you would rather not use that framing, delete the headline's
second sentence — the body stands without it.
