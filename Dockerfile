# SENTINEL Forge — analyst review dashboard (dashboard/backend + dashboard/frontend).
#
# Deliberately does NOT containerise the Scala/Spark compiler side: `dashboard/README.md`
# already states why the dashboard never runs Stage 4 synchronously per request (a cold
# JVM/Spark startup is 10-20+ seconds), and that same cost makes a JVM+Spark base image a poor
# fit for this container's actual job. The compiler's own checks are run directly via
# `sbt runMain ...` (docs/spec/stage4-scala-toolchain.md) — a batch tool, not a service to
# containerise here. This image ships exactly what the dashboard needs: `nlp/src` and
# `compiler/src`'s pure-Python parts, `data/samples`, and the already-computed
# `experiments/results/*.json` the dashboard's "replay results" panel serves (never a live
# Spark re-run — see dashboard/README.md's "Design choice, stated plainly").
FROM python:3.11-slim

WORKDIR /app

# Only the manifest first, so dependency installs cache independently of source changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The dashboard's sys.path inserts (dashboard/backend/main.py) assume it's launched from the repo
# root, so the whole repo (minus .dockerignore's exclusions — generated data, checkpoints,
# toolchains) is copied rather than a hand-picked subset that could silently drift from what
# main.py actually imports.
COPY . .

EXPOSE 8000

# GROQ_API_KEY must be supplied at `docker run`/compose time (see .env.example) for the live
# extraction panel; classical extraction and Stage 3 validation work without it.
CMD ["python", "-m", "uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
