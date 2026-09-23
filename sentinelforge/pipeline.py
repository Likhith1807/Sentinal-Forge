"""Report -> extraction claims -> evidence check -> data check -> compiled rule, in one call.

This is the ONLY path from a report to a compiled rule that the dashboard, the CLI and the evaluation
harness share, so a number measured in evaluation is the number a user gets:

    extractor proposal  --reconcile-->  evidence-backed spec  --check_dataset-->  --compile_spec-->  rule

`status`:
    compiled          every condition evidenced, the data can evaluate it, a rule exists
    needs_review      evidence absent or two readings disagree; nothing compiled
    rejected          positive evidence the report is not expressible / not a supported behaviour
    data_unsupported  the report is fine but this dataset cannot evaluate it (missing/mistyped field)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .compile import RuleBuildError, compile_spec
from .reconcile import ACCEPTED, NEEDS_REVIEW, REJECTED, Reconciliation, reconcile
from .validation import DatasetCheck, check_dataset, default_profile

COMPILED, DATA_UNSUPPORTED = "compiled", "data_unsupported"


@dataclass
class Analysis:
    status: str
    reconciliation: Reconciliation
    dataSupport: DatasetCheck | None = None
    compiled: dict | None = None
    error: str | None = None

    @property
    def behaviourId(self) -> str | None:
        return self.reconciliation.behaviourId

    def to_dict(self) -> dict:
        return {"status": self.status, "behaviourId": self.behaviourId,
                "reconciliation": self.reconciliation.to_dict(),
                "dataSupport": self.dataSupport.to_dict() if self.dataSupport else None,
                "compiled": self.compiled, "error": self.error}


def analyze(report_text: str, extraction: dict | None = None, profile: dict | None = None,
            unavailable: set | None = None) -> Analysis:
    rec = reconcile(report_text, extraction)
    if rec.status == REJECTED:
        return Analysis("rejected", rec)
    if rec.status == NEEDS_REVIEW:
        return Analysis("needs_review", rec)

    support = None
    if profile is not None or unavailable:
        profile = profile if profile is not None else default_profile()
        support = check_dataset(rec.behaviourId, profile, unavailable)
        if not support.supported:
            return Analysis(DATA_UNSUPPORTED, rec, support)
    try:
        compiled = compile_spec(rec.spec)
    except RuleBuildError as exc:                     # reconcile accepted but strict validation refused: never compile
        return Analysis("rejected", rec, support, error=str(exc))
    return Analysis(COMPILED, rec, support, compiled)
