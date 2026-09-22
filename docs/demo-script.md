# Demo video script (2-3 minutes)

A recording script for the dashboard's real demo flow, matching
`dashboard/README.md`'s "demo flow it implements" section exactly — every step below is a real
feature, not staged. Written for whoever records it (see
[`docs/deployment.md`](deployment.md) for getting a URL to record against, or record against
`localhost:8000` from `docker compose up --build`).

**Before recording**: if using a hosted free-tier URL, hit it once ~2 minutes early so the
container is warm (see the Render/Fly cold-start note in `docs/deployment.md`) — a 30-60s
loading spinner at the start of a demo undercuts it for no real reason.

## Shot list

**0:00-0:15 — Open on the report picker.**
Say what this is in one sentence: *"SENTINEL Forge turns a threat report into a Spark detection
rule, but only when the report actually contains enough evidence to justify one — this is that
pipeline, live."* Pick `login-brute-force-001` (or any sample under `data/samples/reports/`).

**0:15-0:45 — Extraction + evidence.**
Run classical extraction. Point out the extracted behaviour, threshold, and time window each
have a highlighted source-text span backing them — say explicitly: *"nothing here is asserted
without a quote from the report."* Switch to the transformer extractor on the same report to
show the second system agreeing (or, if it's a report known to disagree — see
`docs/phase-c-extraction.md` — use that moment to show the confidence panel's
"low confidence — recommend review" state instead; either is a real, honest beat).

**0:45-1:15 — Stage 3 validation, then break it live.**
Show the "supported" verdict against the real log schema. Then uncheck a required field (e.g.
`account_id`) in the field-availability simulator and show the verdict flip to
`rejected`/`insufficient_context` with the missing field named. Say: *"this is the system
refusing to compile a rule it can't actually back with real telemetry — not a crash, a
structured refusal."*

**1:15-1:45 — Real replay results.**
Re-check the field, show the panel of real, already-computed replay results for this behaviour
(17/17 scenarios, from `experiments/results/phase4_replay_check.json`) and, if time allows,
mention the scale number out loud (10,900/10,900 on the real 36.7M-event dataset —
`docs/phase-e-scale.md`) to make clear this isn't a toy-scale demo pretending to be more.

**1:45-2:15 — Analyst decision + audit trail.**
Click approve (or refine, changing the threshold first to show the diff feature). Open the audit
log and point at the version diff on a second decision for the same report — *"every decision is
tied to the exact spec it was made on, and changes are diffed, not just overwritten."*

**2:15-2:30 — Close.**
One sentence on what's NOT live here and why (Scala/Spark and the fine-tuned model are
real-but-precomputed, not re-run per visitor — the design tradeoff `dashboard/README.md`
documents), then point at the README's Results table for the full reproducible numbers.

## What to explicitly avoid staging

- Don't pre-arrange a "perfect" run if the transformer extractor happens to disagree with
  classical on the report you picked — that disagreement IS the confidence signal working
  correctly (see `docs/phase-c-extraction.md`'s Pearson r=0.919 finding); showing it live is more
  credible than avoiding it.
- Don't claim the Scala/Spark numbers were just computed — say plainly they're real,
  already-computed results being served, exactly as `dashboard/README.md` documents.

## After recording

Upload wherever the résumé/portfolio link expects it (YouTube unlisted, Loom, etc.) and add the
link to the README's Results section — that edit isn't done automatically here since it depends
on where you host the video.
