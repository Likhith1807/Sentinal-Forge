"""SENTINEL Forge - analyst API.

    python -m uvicorn dashboard.backend.main:app --port 8000       # from the repository root

Everything the UI shows comes from `sentinelforge.service.workflow.Workspace`; this file only translates HTTP
to that API, enforces access control and input limits, and turns domain errors into honest status codes:

    404 unknown id          409 state conflict / data cannot evaluate the rule (with the exact dependencies)
    422 invalid input       401/403 authentication / access      429 rate limited
    500 anything else       -> generic message + request id; the traceback stays in the server log
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sentinelforge import behaviours as B
from sentinelforge import __version__
from sentinelforge.compile import COMPILER_VERSION
from sentinelforge.service import engines, extractors
from sentinelforge.service.settings import REPO_ROOT, Settings
from sentinelforge.service.workflow import Blocked, InvalidRequest, NotFound, StateConflict, Workspace

from .evaluation import evaluation_payload
from .security import SECURITY_HEADERS, Principal, Security

log = logging.getLogger("sentinelforge.api")
FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SAMPLES_DIR = REPO_ROOT / "data" / "demo" / "reports"


# ------------------------------------------------------------------------------------- bodies
class ReportIn(BaseModel):
    text: str = Field(..., max_length=400_000)
    title: Optional[str] = Field(None, max_length=200)


class AnalysisIn(BaseModel):
    extractor: Optional[str] = Field(None, max_length=32)
    datasetId: Optional[str] = Field(None, max_length=64)


class RunIn(BaseModel):
    datasetId: Optional[str] = Field(None, max_length=64)


class RefineIn(BaseModel):
    overrides: dict
    note: str = Field(..., max_length=1000)


class DecisionIn(BaseModel):
    decision: str = Field(..., max_length=16)
    runId: Optional[str] = Field(None, max_length=64)
    note: str = Field("", max_length=1000)


class SchemaChangeIn(BaseModel):
    drop: list[str] = Field(default_factory=list, max_length=20)
    retype: dict[str, str] = Field(default_factory=dict)


class RestoreIn(BaseModel):
    version: int = Field(..., ge=1)


class DatasetIn(BaseModel):
    name: str = Field(..., max_length=120)
    eventsJsonl: str
    policyJson: Optional[str] = None


# -------------------------------------------------------------------------------------- app
def create_app(settings: Settings | None = None, workspace: Workspace | None = None) -> FastAPI:
    settings = settings or Settings()
    security = Security(settings)
    state: dict = {"ws": workspace}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["ws"] is None:
            state["ws"] = Workspace(settings)
        if security.generated_token:
            print(f"\n  SENTINEL Forge API token (shown once): {security.generated_token}\n", flush=True)
        yield
        state["ws"].shutdown()

    app = FastAPI(title="SENTINEL Forge", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    if settings.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        request.state.id = uuid.uuid4().hex[:12]
        limit = settings.max_upload_bytes * 2 + 4096 if request.url.path == "/api/datasets" else settings.max_report_bytes * 6 + 4096
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > limit:
            return JSONResponse({"error": "request body too large", "requestId": request.state.id}, status_code=413)
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - last line of defence: never leak a traceback to a client
            log.exception("unhandled error [%s] %s %s", request.state.id, request.method, request.url.path)
            response = JSONResponse({"error": "internal error", "requestId": request.state.id}, status_code=500)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(NotFound)
    async def _nf(_, exc):
        return JSONResponse({"error": "not found", "detail": str(exc)}, status_code=404)

    @app.exception_handler(InvalidRequest)
    async def _inv(_, exc):
        return JSONResponse({"error": "invalid request", "detail": str(exc), "issues": exc.issues}, status_code=422)

    @app.exception_handler(StateConflict)
    async def _sc(_, exc):
        return JSONResponse({"error": "state conflict", "detail": str(exc)}, status_code=409)

    @app.exception_handler(Blocked)
    async def _bl(_, exc):
        return JSONResponse({"error": "data cannot evaluate this rule", "detail": str(exc), "dependencies": exc.dependencies}, status_code=409)

    ws = lambda: state["ws"]  # noqa: E731

    def user(request: Request) -> Principal:
        return security.authenticate(request)

    def writer(request: Request) -> Principal:
        p = security.authenticate(request)
        if not p.can_write:
            raise HTTPException(403, "This token is read-only (viewer).")
        security.rate_limit(f"{p.name}:{request.client.host if request.client else '-'}")
        return p

    # --------------------------------------------------------------------------- meta
    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/config")
    def config(p: Principal = Depends(user)):
        return {"version": __version__, "compilerVersion": COMPILER_VERSION, "auth": security.mode, "user": {"name": p.name, "role": p.role},
                "extractors": [e.to_dict() for e in extractors.list_extractors()], "defaultExtractor": extractors.default_extractor(),
                "engine": {"selected": engines.choose(settings.engine), "sparkAvailable": engines.spark_available()[0],
                           "note": engines.spark_available()[1]},
                "limits": {"maxReportBytes": settings.max_report_bytes, "maxUploadBytes": settings.max_upload_bytes},
                "behaviours": [{"id": b.id, "name": b.display_name, "checks": b.checks, "attack": list(b.attack), "recipe": b.recipe,
                                "countSemantics": b.count_semantics, "limitations": list(b.limitations),
                                "logFields": list(b.log_fields), "policyFields": list(b.policy_fields)} for b in B.BEHAVIOURS.values()]}

    @app.get("/api/samples")
    def samples(p: Principal = Depends(user)):
        m = json.loads((SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))
        return {"samples": [{**s, "title": (SAMPLES_DIR / s["file"]).read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()[:90]} for s in m["samples"]]}

    @app.get("/api/samples/{sample_id}")
    def sample(sample_id: str, p: Principal = Depends(user)):
        m = json.loads((SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))
        s = next((x for x in m["samples"] if x["id"] == sample_id), None)
        if s is None:
            raise HTTPException(404, "unknown sample")
        return {**s, "text": (SAMPLES_DIR / s["file"]).read_text(encoding="utf-8")}

    @app.get("/api/overview")
    def overview(p: Principal = Depends(user)):
        return ws().overview()

    @app.get("/api/evaluation")
    def evaluation(p: Principal = Depends(user)):
        return evaluation_payload()

    # ------------------------------------------------------------------------ reports
    @app.post("/api/reports", status_code=201)
    def create_report(body: ReportIn, p: Principal = Depends(writer)):
        return ws().create_report(body.text, body.title, "pasted", p.name)

    @app.get("/api/reports")
    def list_reports(p: Principal = Depends(user)):
        return {"reports": ws().list_reports()}

    @app.get("/api/reports/{report_id}")
    def get_report(report_id: str, p: Principal = Depends(user)):
        return ws().get_report(report_id)

    @app.post("/api/reports/{report_id}/analyses", status_code=202)
    def analyse(report_id: str, body: AnalysisIn, p: Principal = Depends(writer)):
        return ws().submit_analysis(report_id, body.extractor, body.datasetId, p.name)

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(analysis_id: str, p: Principal = Depends(user)):
        a = ws().get_analysis(analysis_id)
        a["report"] = ws().get_report(a["report_id"])
        a["job"] = ws().jobs.get(a["jobId"]) if a.get("jobId") else None
        return a

    @app.get("/api/analyses/{analysis_id}/data-support")
    def data_support(analysis_id: str, datasetId: Optional[str] = Query(None, max_length=64), p: Principal = Depends(user)):
        """Which conditions of the analysed behaviour the chosen dataset can (not) evaluate, field by field."""
        from sentinelforge.service import datasets as ds
        from sentinelforge.service.schema_impact import analyse_rule
        a = ws().get_analysis(analysis_id)
        res = a.get("result") or {}
        bid = (res.get("reconciliation") or {}).get("behaviourId")
        if not bid:
            return {"behaviourId": None, "dataset": None, "dependencies": [], "conditions": []}
        d = ws().get_dataset(datasetId or (res.get("dataset") or {}).get("id") or "demo-auth")
        out = analyse_rule(bid, res.get("compiled") or {}, d["profile"])
        return {"behaviourId": bid, "dataset": {k: d[k] for k in ("id", "name", "kind", "version", "fingerprint")},
                "supported": not out["affected"], "dependencies": out["dependencies"],
                "conditions": out["blockedConditions"] + out["evaluableConditions"], "blocked": out["blockedConditions"]}

    @app.post("/api/analyses/{analysis_id}/rule", status_code=201)
    def make_rule(analysis_id: str, p: Principal = Depends(writer)):
        return ws().create_rule_version(analysis_id, p.name)

    # --------------------------------------------------------------------------- rules
    @app.get("/api/rules")
    def rules(p: Principal = Depends(user)):
        return {"ruleVersions": ws().list_rule_versions()}

    @app.get("/api/rule-versions/{rv_id}")
    def rule_version(rv_id: str, p: Principal = Depends(user)):
        rv = ws().get_rule_version(rv_id)
        a = ws().get_analysis(rv["analysis_id"])
        rv["analysis"] = {"id": a["id"], "status": a["status"], "extractor": a["extractor"], "result": a["result"]}
        rv["report"] = ws().get_report(a["report_id"])
        return rv

    @app.post("/api/rule-versions/{rv_id}/refine", status_code=201)
    def refine(rv_id: str, body: RefineIn, p: Principal = Depends(writer)):
        return ws().refine_rule_version(rv_id, body.overrides, body.note, p.name)

    @app.post("/api/rule-versions/{rv_id}/runs", status_code=202)
    def start_run(rv_id: str, body: RunIn, p: Principal = Depends(writer)):
        return ws().submit_run(rv_id, body.datasetId, p.name)

    @app.post("/api/rule-versions/{rv_id}/decision")
    def decide(rv_id: str, body: DecisionIn, p: Principal = Depends(writer)):
        return ws().decide(rv_id, body.decision, p.name, body.note, body.runId)

    @app.post("/api/rule-versions/{rv_id}/resume", status_code=202)
    def resume(rv_id: str, p: Principal = Depends(writer)):
        return ws().resume_rule_version(rv_id, p.name)

    @app.get("/api/rule-versions/{rv_id}/sigma")
    def sigma(rv_id: str, p: Principal = Depends(user)):
        from sentinelforge.sigma import export_rule
        return export_rule(ws().get_rule_version(rv_id))

    # ---------------------------------------------------------------------------- jobs / runs
    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, p: Principal = Depends(user)):
        j = ws().jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "unknown job")
        return j

    @app.get("/api/runs/{run_id}")
    def run(run_id: str, p: Principal = Depends(user)):
        r = ws().get_run(run_id)
        r.pop("run_dir", None)                                     # server paths are not for clients
        r["job"] = ws().jobs.get(r["jobId"]) if r.get("jobId") else None
        return r

    @app.get("/api/runs/{run_id}/alerts")
    def alerts(run_id: str, status: Optional[str] = Query(None, max_length=32), limit: int = Query(200, ge=1, le=1000),
               offset: int = Query(0, ge=0), p: Principal = Depends(user)):
        return ws().alerts(run_id, status, limit, offset)

    @app.get("/api/runs/{run_id}/alerts/{alert_id}/evidence")
    def evidence_record(run_id: str, alert_id: str, p: Principal = Depends(user)):
        return ws().evidence_record(run_id, alert_id)

    @app.get("/api/runs/{run_id}/quarantine")
    def quarantine(run_id: str, p: Principal = Depends(user)):
        return {"rows": ws().quarantine(run_id)}

    # ----------------------------------------------------------------------------- datasets
    @app.get("/api/datasets")
    def datasets(p: Principal = Depends(user)):
        from sentinelforge.service import datasets as ds
        return {"datasets": ds.list_all(ws().store)}

    @app.get("/api/datasets/{dataset_id}")
    def dataset(dataset_id: str, p: Principal = Depends(user)):
        d = ws().get_dataset(dataset_id)
        d.pop("eventsPath", None), d.pop("policyPath", None)
        return d

    @app.post("/api/datasets", status_code=201)
    def upload(body: DatasetIn, p: Principal = Depends(writer)):
        d = ws().register_upload(body.name, body.eventsJsonl.encode("utf-8"), body.policyJson.encode("utf-8") if body.policyJson else None, p.name)
        d.pop("eventsPath", None), d.pop("policyPath", None)
        return d

    @app.post("/api/datasets/{dataset_id}/schema-change")
    def schema_change(dataset_id: str, body: SchemaChangeIn, p: Principal = Depends(writer)):
        return ws().apply_schema_change(dataset_id, body.drop, body.retype, p.name)

    @app.post("/api/datasets/{dataset_id}/restore")
    def restore(dataset_id: str, body: RestoreIn, p: Principal = Depends(writer)):
        return ws().restore_dataset(dataset_id, body.version, p.name)

    # -------------------------------------------------------------------------- static UI
    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz():
        return "ok"

    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
    return app


app = create_app()
