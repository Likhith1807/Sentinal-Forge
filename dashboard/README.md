# Dashboard

A FastAPI backend (`backend/`) and a dependency-free, build-step-free frontend (`frontend/`, ES modules) over the service layer in
`sentinelforge/service/`. Five screens, one workflow.

```
python -m uvicorn dashboard.backend.main:app --port 8000        # from the repo root; http://127.0.0.1:8000
```

## The workflow it implements

| screen | what happens there |
|---|---|
| **Overview** | Where things stand: rules by state, recent runs, paused rules and why, open reviews. Every dataset is labelled *fresh*, *archived* or *demo*. |
| **Report workspace** | Paste a threat report. Pick an extractor (default: evidence-only — no model). The analysis runs as a background job with a visible state (`queued → extracting → validating → ready / needs review / rejected / failed`). The extracted conditions are shown with the exact quote, and its position, for each value. |
| **Validation review** | What the report says vs. what the data can support, with an actionable message for each problem. Refine a value (threshold, window) — the server rebuilds and re-validates the rule; the browser never posts a rule. Compile → a rule **version** with a hash. Run it as a fresh background job on a dataset version, and approve or reject: the decision references the server's rule version and the run it was made on. Also the **schema-change impact** panel. |
| **Investigation** | Pick an alert and follow its reasoning back to the report: the six questions of the evidence record (why, means, supported, fired, executed, uncertain), the matching events, quarantined rows. |
| **Evaluation** | Archived measurements, each labelled with what it is (regression / frozen holdout / synthetic) and how to reproduce it. Nothing on this screen is computed live. |

The same workflow, scripted with real output: `python scripts/demo.py`.

## Things the interface is deliberately careful about

* **No "confidence".** A model's probability is not evidence and is not shown as such. The interface shows *quotes* and *reason codes*.
* **State is words as well as colour.** Every status carries a text label and an icon; colour is restrained and never the only signal.
* **Loading, empty, rejected and failed states exist** for every screen, and they say what happened and what to do next.
* **Results are tied to** a report, a rule version, a dataset version and a run. A result for an older rule version says so.
* **Fresh / archived / demo** labels stay next to any number that is not from the current session.
* **Schema changes** (Validation review → Schema-change impact) show which approved rules the change pauses and which it leaves alone, and a paused rule
  cannot resume until it has been revalidated against the current schema.

## API (all under `/api`)

`GET /health · /config · /samples · /overview · /evaluation` — `POST /reports · GET /reports/{id}` — `POST /reports/{id}/analyses`
(202, returns a job) — `GET /analyses/{id} · /analyses/{id}/data-support` — `POST /analyses/{id}/rule` — `POST /rule-versions/{id}/refine ·
/runs · /decision · /resume` — `GET /rule-versions/{id}/sigma` (only when the rule survives export unchanged) — `GET /jobs/{id} · /runs/{id} ·
/runs/{id}/alerts · /runs/{id}/alerts/{alertId}/evidence · /runs/{id}/quarantine` — `GET/POST /datasets · POST /datasets/{id}/schema-change ·
/restore`. Interactive documentation is at `/docs` in local mode.

## Security modes

*Local demo* (default): binds to loopback only, no login, single analyst identity. *Token mode* (`SF_API_TOKENS="name:token[:role],…"`,
required for any non-loopback bind): constant-time bearer comparison, roles (`analyst` / `viewer`), rate limit, size limits, CSP, generic
500s. Details and limits: `docs/deployment.md`, `docs/limitations.md`.

## Configuration

`SF_STATE_DIR` (default `var/`), `SF_ENGINE` (`auto | spark | reference`), `SF_JOB_WORKERS`, `SF_MAX_REPORT_BYTES`, `SF_MAX_UPLOAD_BYTES`,
`SF_RUN_TIMEOUT`, `SF_SPARK_HEAP`, `SF_API_TOKENS`, `SF_REQUIRE_AUTH`, `SF_CORS_ORIGINS`, `SF_RATE_LIMIT`.

Tests: `tests/service/` (workflow, concurrency, API, security).
