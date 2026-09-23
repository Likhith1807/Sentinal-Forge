# Deploying the dashboard publicly

The dashboard runs locally with `docker compose up --build` (see the root `README.md`
Quickstart). This doc covers putting that same container behind a public URL, which needs
hosting credentials this environment doesn't have — so these are the exact steps to run
yourself, not something already done.

Both paths below deploy **only the dashboard** (FastAPI + the classical/prompted extractors +
Stage 3 validation), matching `dashboard/README.md`'s stated design boundary: the Scala/Spark
compiler side is never invoked synchronously per request, locally or in production. What a
visitor sees live is real extraction and real validation; what they see for compiler/fine-tuned
results is the real, already-computed JSON under `experiments/results/`, exactly as it behaves
locally.

## Before either path

1. Push this repo to GitHub — already done
   ([github.com/Likhith1807/Sentinal-Forge](https://github.com/Likhith1807/Sentinal-Forge)), which
   is also what makes `.github/workflows/ci.yml` run (see the badge at the top of `README.md`).
   Render and Fly can also deploy straight from a local `Dockerfile` via their CLI without GitHub,
   if you'd rather not connect the repo.
2. Have a Groq API key ready (`.env.example` shows the variable name) if you want the live
   prompted-extraction panel to work publicly. Everything else — classical extraction, Stage 3
   validation, the replay/compiler results panels — works with no key at all.
3. **Never commit the real key.** Both platforms below take it as a dashboard/CLI secret, not a
   file in this repo.

## Option A: Render (`render.yaml`, recommended for a first deploy — no CLI install needed)

1. Push to GitHub.
2. https://dashboard.render.com -> **New** -> **Blueprint** -> select this repo. Render reads
   `render.yaml` at the repo root automatically.
3. In the created service's **Environment** tab, set `GROQ_API_KEY` (the blueprint deliberately
   leaves this blank — `sync: false` — so it's never in git history).
4. Click **Deploy**. First build takes a few minutes (installs `requirements.txt`, including
   `transformers`/`torch` — the same weight the local Docker build has).
5. Render gives you a `https://sentinel-forge-dashboard-<hash>.onrender.com` URL.

**Free-tier caveat to mention out loud in a demo, not hide**: Render's free web services sleep
after ~15 minutes idle and take 30-60s to wake on the next request. If recording a demo video,
hit the URL once a minute or two before recording so it's already warm.

## Option B: Fly.io (`fly.toml`, a bit more setup, scales to zero, generally faster cold start)

1. Install `flyctl` (https://fly.io/docs/flyctl/install/) and `flyctl auth login`.
2. From the repo root: `flyctl launch --no-deploy` — it will detect `fly.toml` and offer to
   reuse it; say yes rather than letting it generate a new one.
3. `flyctl secrets set GROQ_API_KEY=<your key>`
4. `flyctl deploy`
5. `flyctl open` — opens the live `https://sentinel-forge-dashboard.fly.dev` URL.

## Verifying it after either deploy

Open the URL, pick a sample report, and walk through the same flow `scripts/demo.py` walks
through locally (extract -> validate -> degrade a field -> see the real replay/compiler
numbers) — see [`docs/demo-script.md`](demo-script.md) for the exact walkthrough to record.

## What's intentionally not deployed

- The Scala/Spark compiler and its checks (`GeneratedDataCheck`, `StreamingRecoveryCheck`,
  etc.) — these are real, separately-run evaluations (see `docs/phase-e-scale.md`), not a
  service. Their results are served as static JSON from `experiments/results/`.
- Fine-tuned model inference — `docs/phase-c-extraction.md`'s comparison numbers are
  precomputed and served the same way, not re-run per visitor.
