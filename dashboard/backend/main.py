"""SENTINEL Forge analyst review dashboard — backend.

Wires together components already built and verified in earlier phases:
nlp/src (extraction + injection guard), compiler/src (Stage 3 validation),
and the real, already-computed replay results from experiments/results/.

Design choice, stated plainly: this does NOT re-run the Scala/Spark
compiler (Stage 4) synchronously per HTTP request — a cold JVM/Spark
startup is 10-20+ seconds, which is a bad fit for an interactive request
and would need a long-running Spark session this dashboard doesn't manage.
Extraction and Stage 3 validation (both pure Python, no JVM) run live and
fast; the "replay results" panel serves the real, already-verified output
from Phase 4/5's actual Spark runs (experiments/results/*.json) rather
than faking a live re-run. That boundary is documented here, not hidden.

Run:
    pip install fastapi uvicorn
    python -m uvicorn dashboard.backend.main:app --reload --port 8000
    (from the repo root, so the relative sys.path inserts below resolve)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))
sys.path.insert(0, str(REPO_ROOT / "compiler" / "src"))

import classical_extractor  # noqa: E402
import transformer_extractor  # noqa: E402
import injection_guard  # noqa: E402
import observability_checker as stage3  # noqa: E402

REPORTS_DIR = REPO_ROOT / "data" / "samples" / "reports"
ADVERSARIAL_DIR = REPO_ROOT / "compiler" / "test" / "fixtures" / "adversarial"
RESULTS_DIR = REPO_ROOT / "experiments" / "results"
AUDIT_LOG_PATH = Path(__file__).parent / "audit_log.json"
BEHAVIOURS_DOC = REPO_ROOT / "docs" / "behaviours.md"

app = FastAPI(title="SENTINEL Forge Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BEHAVIOUR_MANIFEST = [
    {"id": "repeated-failed-login-then-success", "shortName": "Repeated Failed Login → Success", "attack": ["T1110", "T1078"]},
    {"id": "password-spray-across-accounts", "shortName": "Password Spray", "attack": ["T1110.003"]},
    {"id": "concurrent-sessions-different-hosts", "shortName": "Concurrent Sessions", "attack": ["T1078"]},
    {"id": "service-account-interactive-auth", "shortName": "Service Account Interactive Auth", "attack": ["T1078.003"]},
    {"id": "mfa-bypass-on-required-account", "shortName": "MFA Bypass", "attack": ["T1621", "T1556"]},
]

REPORT_MANIFEST = {
    "login-brute-force-001": {"behaviourId": "repeated-failed-login-then-success", "split": "train", "kind": "original"},
    "login-brute-force-002": {"behaviourId": "repeated-failed-login-then-success", "split": "train", "kind": "paraphrase"},
    "login-brute-force-003": {"behaviourId": "repeated-failed-login-then-success", "split": "held-out", "kind": "distinct-incident"},
    "password-spray-001": {"behaviourId": "password-spray-across-accounts", "split": "train", "kind": "original"},
    "password-spray-002": {"behaviourId": "password-spray-across-accounts", "split": "train", "kind": "paraphrase"},
    "password-spray-003": {"behaviourId": "password-spray-across-accounts", "split": "held-out", "kind": "distinct-incident"},
    "concurrent-sessions-001": {"behaviourId": "concurrent-sessions-different-hosts", "split": "train", "kind": "original"},
    "concurrent-sessions-002": {"behaviourId": "concurrent-sessions-different-hosts", "split": "train", "kind": "paraphrase"},
    "concurrent-sessions-003": {"behaviourId": "concurrent-sessions-different-hosts", "split": "held-out", "kind": "distinct-incident"},
    "service-account-auth-001": {"behaviourId": "service-account-interactive-auth", "split": "train", "kind": "original"},
    "service-account-auth-002": {"behaviourId": "service-account-interactive-auth", "split": "train", "kind": "paraphrase"},
    "service-account-auth-003": {"behaviourId": "service-account-interactive-auth", "split": "held-out", "kind": "distinct-incident"},
    "mfa-bypass-001": {"behaviourId": "mfa-bypass-on-required-account", "split": "train", "kind": "original"},
    "mfa-bypass-002": {"behaviourId": "mfa-bypass-on-required-account", "split": "train", "kind": "paraphrase"},
    "mfa-bypass-003": {"behaviourId": "mfa-bypass-on-required-account", "split": "held-out", "kind": "distinct-incident"},
}

ADVERSARIAL_MANIFEST = {
    "injection-001-ignore-instructions": {"label": "Direct instruction injection"},
    "injection-002-fake-policy-override": {"label": "Fake system-note policy override"},
    "injection-003-benign-report-no-injection": {"label": "Benign report (negative control)"},
}


def _read_audit_log() -> list:
    if not AUDIT_LOG_PATH.exists():
        return []
    return json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))


def _write_audit_log(entries: list) -> None:
    AUDIT_LOG_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _jaccard(a: set, b: set) -> float:
    """Same formula as experiments/results/calibration_analysis.py, which
    validated this signal (Pearson r=0.919, n=5) as a real proxy for
    transformer-extraction correctness — see experiments/results/README.md.
    Not a new score invented for the dashboard; the same one, reused."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _find_report_path(report_id: str) -> Path:
    if report_id in REPORT_MANIFEST:
        return REPORTS_DIR / f"{report_id}.md"
    if report_id in ADVERSARIAL_MANIFEST:
        return ADVERSARIAL_DIR / f"{report_id}.md"
    raise HTTPException(404, f"Unknown report id: {report_id}")


@app.get("/api/reports")
def list_reports():
    real = [{"id": rid, **meta} for rid, meta in REPORT_MANIFEST.items()]
    adversarial = [{"id": rid, "adversarial": True, **meta} for rid, meta in ADVERSARIAL_MANIFEST.items()]
    return {"reports": real, "adversarialFixtures": adversarial}


@app.get("/api/reports/{report_id}")
def get_report(report_id: str):
    path = _find_report_path(report_id)
    return {"id": report_id, "text": path.read_text(encoding="utf-8")}


@app.get("/api/behaviours")
def get_behaviours():
    return {"behaviours": BEHAVIOUR_MANIFEST}


class AnalyzeRequest(BaseModel):
    report_id: str
    extractor: str = "transformer"  # "classical" | "transformer"
    override_injection_block: bool = False
    remove_fields: Optional[list] = None  # simulates a field becoming unavailable


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    path = _find_report_path(req.report_id)
    text = path.read_text(encoding="utf-8")

    guard = injection_guard.scan(text)
    guard_payload = {"flagged": guard.flagged, "matches": guard.matches}

    if req.extractor == "transformer" and guard.flagged and not req.override_injection_block:
        return {
            "reportId": req.report_id,
            "blocked": True,
            "reason": "Prompt-injection guard flagged this report before any LLM call was made. "
                      "The transformer extractor was not run. Classical (regex-based) extraction is "
                      "unaffected, since it never sends report text to a model - set "
                      "override_injection_block=true to run the LLM extractor anyway.",
            "injectionGuard": guard_payload,
        }

    confidence = None
    if req.extractor == "classical":
        result = classical_extractor.extract(text)
        spec = {
            "behaviourId": result.behaviourId,
            "requiredFields": result.requiredFields,
            "policyFields": result.policyFields,
            "threshold": result.threshold,
            "timeWindow": result.timeWindow,
            "provenance": result.provenance,
        }
    else:
        spec = transformer_extractor.extract(text)
        # Cross-extractor agreement: classical is cheap/local/no network call,
        # so computing it as a second opinion costs nothing extra — this is
        # the real, validated confidence signal from Phase 5's calibration
        # analysis, not a new one invented for the UI.
        classical_result = classical_extractor.extract(text)
        transformer_fields = set(spec["requiredFields"]) | set(spec["policyFields"])
        classical_fields = set(classical_result.requiredFields) | set(classical_result.policyFields)
        agreement = _jaccard(transformer_fields, classical_fields)
        confidence = {
            "agreement": round(agreement, 3),
            "level": "high" if agreement == 1.0 else "low",
            "recommendReview": agreement < 1.0,
            "classicalFields": sorted(classical_fields),
            "transformerFields": sorted(transformer_fields),
            "note": "Cross-extractor agreement (classical vs transformer field sets) - validated in "
                    "Phase 5 as a real confidence proxy (Pearson r=0.919, n=5): full agreement predicted "
                    "correct extraction in every held-out case; any disagreement predicted an imperfect one.",
        }

    if req.remove_fields:
        spec = dict(spec)
        spec["requiredFields"] = [f for f in spec["requiredFields"] if f not in req.remove_fields]
        spec["policyFields"] = [f for f in spec["policyFields"] if f not in req.remove_fields]

    validation = stage3.validate(spec)

    return {
        "reportId": req.report_id,
        "blocked": False,
        "extractor": req.extractor,
        "injectionGuard": guard_payload,
        "spec": spec,
        "confidence": confidence,
        "stage3": {
            "status": validation.status,
            "missingFields": validation.missingFields,
            "unreliableFields": validation.unreliableFields,
            "notes": validation.notes,
        },
        "simulatedRemovedFields": req.remove_fields or [],
    }


@app.get("/api/replay-results")
def replay_results():
    def load(name: str):
        p = RESULTS_DIR / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    return {
        "sentinelForgeFull": load("phase4_replay_check.json"),
        "manualBaseline": load("phase5_manual_baseline_check.json"),
        "streaming": load("phase4_streaming_check.json"),
        "robustness": load("phase5_robustness_check.json"),
        "extractionEval": load("phase2_extraction_eval.json"),
    }


class DecisionRequest(BaseModel):
    report_id: str
    decision: str  # "approve" | "refine"
    analyst: str = "demo-analyst"
    note: str = ""
    spec: Optional[dict] = None  # the spec this decision was made on, for version diffing


def _diff_specs(previous: Optional[dict], current: Optional[dict]) -> Optional[dict]:
    """Diffs two specs' behaviourId/requiredFields/policyFields/threshold/
    timeWindow. Returns None if there's no previous version for this report
    (first decision) or no spec was supplied for the current one."""
    if previous is None or current is None:
        return None
    prev_fields = set(previous.get("requiredFields", [])) | set(previous.get("policyFields", []))
    curr_fields = set(current.get("requiredFields", [])) | set(current.get("policyFields", []))
    return {
        "behaviourIdChanged": previous.get("behaviourId") != current.get("behaviourId"),
        "fieldsAdded": sorted(curr_fields - prev_fields),
        "fieldsRemoved": sorted(prev_fields - curr_fields),
        "thresholdChanged": previous.get("threshold") != current.get("threshold"),
        "previousThreshold": previous.get("threshold"),
        "currentThreshold": current.get("threshold"),
        "timeWindowChanged": previous.get("timeWindow") != current.get("timeWindow"),
        "previousTimeWindow": previous.get("timeWindow"),
        "currentTimeWindow": current.get("timeWindow"),
    }


@app.post("/api/decisions")
def record_decision(req: DecisionRequest):
    if req.decision not in ("approve", "refine"):
        raise HTTPException(400, "decision must be 'approve' or 'refine'")
    entries = _read_audit_log()

    previous_for_report = next(
        (e for e in reversed(entries) if e["reportId"] == req.report_id and e.get("spec")), None
    )
    diff = _diff_specs(previous_for_report["spec"] if previous_for_report else None, req.spec)

    entry = {
        "reportId": req.report_id,
        "decision": req.decision,
        "analyst": req.analyst,
        "note": req.note,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "spec": req.spec,
        "diffFromPreviousVersion": diff,
    }
    entries.append(entry)
    _write_audit_log(entries)
    return {"entry": entry, "log": entries}


@app.get("/api/audit-log")
def get_audit_log():
    return {"log": _read_audit_log()}


# Serve the frontend last, so /api/* routes above take precedence.
app.mount("/", StaticFiles(directory=str(Path(__file__).parent.parent / "frontend"), html=True), name="frontend")
