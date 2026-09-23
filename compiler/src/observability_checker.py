"""Stage 3 - Validate the specification against the documented log schema and policy reference.

Entry point kept for the harnesses, dashboard and tests; the logic lives in `sentinelforge.validation`
(strict spec checks + typed dataset-dependency checks, driven by the single behaviour registry).

What changed, and why (audit of the fine-tuned pipeline, 2026-09):

* Fields the *recipe* reads are fixed by the recipe, so they are checked against the data whether or not
  the extractor happened to list them. Previously a spec that forgot to list `source_host` was REJECTED
  even though the rule would have read it correctly - 8 of 40 supported reports were refused for what
  was really a gap in a model's field list, not in the data.
* Every number is checked as a number: booleans, NaN/inf, numeric strings, fractional counts,
  sub-second windows and conflicting keys are all rejected (see `sentinelforge.validation`).
* The check now covers every field the rule reads OR outputs (event_id and timestamp included) and
  their types, not only the fields the report happened to mention.

Round-1/2 corrections from the earlier independent review (registry check for behaviourId, zero-field
spec, positive thresholds, non-crashing malformed blocks, simulated-unavailable fields) all still hold
and are covered by test_observability_checker.py.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sentinelforge import behaviours as B  # noqa: E402
from sentinelforge.validation import (LOG_SCHEMA_PATH, POLICY_SCHEMA_PATH, check_dataset, default_profile,  # noqa: E402,F401
                                      validate_spec)


@dataclass
class ValidationResult:
    status: str  # "supported" | "rejected"
    missingFields: list = field(default_factory=list)
    unreliableFields: list = field(default_factory=list)
    invalidValues: list = field(default_factory=list)   # spec paths that are malformed, e.g. "threshold"
    notes: list = field(default_factory=list)
    issues: list = field(default_factory=list)          # machine-readable {code, path, message}
    dependencies: list = field(default_factory=list)    # per-field data-support table


def validate(spec: dict, unavailable_fields: set[str] | None = None, profile: dict | None = None) -> ValidationResult:
    """@param unavailable_fields: names to treat as absent from the data regardless of the schema (the
    dashboard's "simulate a field becoming unavailable"). The spec still asks for what it always asked
    for; the data now provides less - so the verdict changes for the right reason.
    @param profile: a dataset profile ({columns, policyColumns}); defaults to the documented schema."""
    unavailable_fields = set(unavailable_fields or ())
    sv = validate_spec(spec)
    if any(i.code == "UNKNOWN_BEHAVIOUR" for i in sv.issues) or (not sv.issues and False):
        return ValidationResult("rejected", notes=[i.message for i in sv.issues], issues=[i.to_dict() for i in sv.issues])
    if not isinstance(spec, dict):
        return ValidationResult("rejected", notes=[i.message for i in sv.issues], issues=[i.to_dict() for i in sv.issues])
    if not (spec.get("requiredFields") or spec.get("policyFields")):
        return ValidationResult("rejected", notes=["Spec names zero fields - nothing to compile. A supported spec must "
                                                   "request at least one observable field."])

    bid = B.canonical_id(spec["behaviourId"])
    beh = B.get(bid)
    # Data-support and spec-validity are independent findings; report both rather than stopping at the first.
    check = check_dataset(bid, profile or default_profile(), unavailable_fields)
    missing = sorted(d.field for d in check.dependencies if d.status in ("missing", "simulated_missing", "wrong_type"))
    unreliable = sorted(d.field for d in check.dependencies if d.status == "unreliable")
    notes = [f"{i.path}: {i.message}" for i in sv.issues]
    for d in check.dependencies:
        if d.status == "simulated_missing":
            notes.append(f"'{d.field}' is being simulated as unavailable in the schema.")
        elif d.status == "missing":
            notes.append(f"'{d.field}' is required by {bid} ({d.role}) but {d.detail}.")
        elif d.status == "wrong_type":
            notes.append(f"'{d.field}' has the wrong type: {d.detail}.")
        elif d.status == "unreliable":
            notes.append(f"'{d.field}' exists in the log schema but is annotated observability=unreliable - a rule must not depend on it.")
    # fields the SPEC itself names: a field the schema has never heard of, or that is unreliable, is a requirement
    # the data cannot meet even when the recipe would not have read it.
    known = set(beh.log_fields) | set(beh.policy_fields)
    log_props = json.loads(LOG_SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]
    for f in list(spec.get("requiredFields", [])) + list(spec.get("policyFields", [])):
        if not isinstance(f, str) or f in known:
            continue
        bare = f.removeprefix("policy.")
        if f in unavailable_fields or bare in unavailable_fields:
            missing.append(f)
            notes.append(f"'{f}' is being simulated as unavailable in the schema.")
        elif bare not in log_props and bare not in ("expected_auth_method", "mfa_required"):
            missing.append(f)
            notes.append(f"'{f}' is not a field in {LOG_SCHEMA_PATH.name} - cannot compile a rule that reads it.")
        elif log_props.get(bare, {}).get("observability") == "unreliable":
            unreliable.append(f)
            notes.append(f"'{f}' exists in the log schema but is annotated observability=unreliable "
                         f"({log_props[bare].get('description', '')}) - a rule must not depend on it.")
    invalid = sorted({i.path.split(".")[0] for i in sv.issues})
    status = "rejected" if (missing or unreliable or sv.issues) else "supported"
    if status == "supported":
        notes.append("All fields the compiled rule reads or emits are present, correctly typed and reliably observable.")
    return ValidationResult(status, missingFields=sorted(set(missing)), unreliableFields=sorted(set(unreliable)),
                            invalidValues=invalid, notes=notes, issues=[i.to_dict() for i in sv.issues],
                            dependencies=[d.to_dict() for d in check.dependencies])
