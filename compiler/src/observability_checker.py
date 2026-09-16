"""Stage 3 — Validate the Specification.

Checks a behaviour spec's requiredFields/policyFields (the shape Phase 2's
extractors produce — see nlp/src/schema_fields.py) against the actual log
schema and account-policy reference, and decides: supported, or rejected
with a specific reason per field.

Prototype note: this is a Python implementation of Stage 3's logic, not the
Scala the project's README specifies for the compiler. This environment has
Java 8 but no Scala/sbt installed (checked directly, not assumed), and
installing a full Scala toolchain is a heavier, slower action than this
session's scope — tracked as an open item for Phase 4, when the compiler
actually needs to emit and run real Spark code, not deferred silently. The
validation logic itself (the actual contribution) doesn't depend on which
language it's written in.

IMPORTANT — what this checker does NOT do: it only checks whether the
NAMED fields are observable. It cannot tell you a spec is missing a field
it should have asked for, or that a behaviourId was misidentified — see
docs/spec/stage3-observability-checker.md for why that's a real, permanent
boundary, not a gap to be closed here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_SCHEMA_PATH = REPO_ROOT / "data" / "samples" / "schema" / "authentication_log_schema.json"
POLICY_SCHEMA_PATH = REPO_ROOT / "data" / "samples" / "schema" / "account_policy_reference.json"


@dataclass
class ValidationResult:
    status: str  # "supported" | "rejected"
    missingFields: list = field(default_factory=list)
    unreliableFields: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _load_log_schema() -> dict:
    return json.loads(LOG_SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_policy_fields() -> set[str]:
    policy_doc = json.loads(POLICY_SCHEMA_PATH.read_text(encoding="utf-8"))
    sample_record = policy_doc["records"][0]
    # account_id/account_type are join/context keys, not behaviour-relevant
    # value fields — excluded the same way nlp/src/schema_fields.py's
    # POLICY_FIELDS only lists the two fields a behaviour can actually key on.
    return {k for k in sample_record if k not in ("account_id", "account_type")}


def validate(spec: dict) -> ValidationResult:
    log_schema = _load_log_schema()
    log_properties = log_schema["properties"]
    policy_fields = _load_policy_fields()

    missing: list[str] = []
    unreliable: list[str] = []
    notes: list[str] = []

    # A spec whose behaviourId couldn't be matched to any of the project's
    # known behaviours (see nlp/src/transformer_extractor.py's
    # "unrecognized:" convention) is unsupported regardless of whether its
    # named fields are individually observable — see
    # docs/spec/stage3-observability-checker.md for the real case (a
    # geo-anomaly report) that surfaced this: every field it asked for was
    # valid, but the report described a behaviour with no representation in
    # this schema at all, and only the missing behaviourId match revealed
    # that. Field-only checking would have silently approved it.
    behaviour_id = spec.get("behaviourId")
    if behaviour_id is not None and str(behaviour_id).startswith("unrecognized:"):
        return ValidationResult(
            status="rejected",
            notes=[f"behaviourId {behaviour_id!r} does not match any known, schema-validated behaviour pattern."],
        )

    for f in spec.get("requiredFields", []):
        if f not in log_properties:
            missing.append(f)
            notes.append(f"'{f}' is not a field in {LOG_SCHEMA_PATH.name} — cannot compile a rule that reads it.")
        elif log_properties[f].get("observability") == "unreliable":
            unreliable.append(f)
            notes.append(
                f"'{f}' exists in the log schema but is annotated observability=unreliable "
                f"({log_properties[f].get('description', '')}) — a rule must not depend on it."
            )

    for pf in spec.get("policyFields", []):
        bare_name = pf.removeprefix("policy.")
        if bare_name not in policy_fields:
            missing.append(pf)
            notes.append(f"'{pf}' is not a field in {POLICY_SCHEMA_PATH.name} — cannot compile a rule that reads it.")

    status = "rejected" if (missing or unreliable) else "supported"
    if status == "supported":
        notes.append("All requested fields are present and reliably observable.")

    return ValidationResult(status=status, missingFields=missing, unreliableFields=unreliable, notes=notes)
