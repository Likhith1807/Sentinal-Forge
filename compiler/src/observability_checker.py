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

CORRECTION, round 1 (independent external review, 2026-09-16): an earlier
version accepted an empty spec, an arbitrary/unknown behaviourId that
didn't happen to use the "unrecognized:" string prefix convention, and a
spec with negative or zero threshold/time-window values — all as
"supported." Fixed: behaviourId checked against the real registry, a
zero-field spec rejected, threshold/timeWindow sanity-checked.

CORRECTION, round 2 (same reviewer, same day): round 1's own fix had 2 new
bugs, both reproduced and fixed here:
  1. `block[value_key]` crashed with KeyError on a timeWindow missing
     "amount" instead of rejecting it like any other malformed spec.
  2. This file's docstring previously claimed "cannot tell you a spec is
     missing a field it should have asked for" as a PERMANENT boundary —
     that claim was wrong once compiler/src/spec_bridge.py's closed recipe
     templates existed: because every known behaviour's recipe shape is
     fixed, its field dependencies (grouping key, event_type, timestamp for
     windowed recipes, the account_id join key for PolicyCompare) are
     fully knowable in advance, not something only the report can reveal.
     The reviewer's reproduction: a spec naming only "timestamp" for
     repeated-failed-login-then-success (silently missing account_id,
     which that recipe's groupingKey structurally requires) validated as
     "supported." Fixed below by cross-checking every known behaviour's
     structural dependencies, imported from spec_bridge.py so the two
     files can't drift out of sync with each other.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_SCHEMA_PATH = REPO_ROOT / "data" / "samples" / "schema" / "authentication_log_schema.json"
POLICY_SCHEMA_PATH = REPO_ROOT / "data" / "samples" / "schema" / "account_policy_reference.json"

sys.path.insert(0, str(REPO_ROOT / "nlp" / "src"))
from schema_fields import BEHAVIOUR_IDS  # noqa: E402 — single source of truth, not duplicated here

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spec_bridge import structural_dependencies  # noqa: E402 — ditto: recipe shape lives in one place


@dataclass
class ValidationResult:
    status: str  # "supported" | "rejected"
    missingFields: list = field(default_factory=list)
    unreliableFields: list = field(default_factory=list)
    invalidValues: list = field(default_factory=list)  # e.g. "threshold" when non-positive — not a field name
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


def validate(spec: dict, unavailable_fields: set[str] | None = None) -> ValidationResult:
    """@param unavailable_fields: field names to treat as NOT observable
    regardless of what the real schema says — lets a caller (the dashboard's
    "simulate a field becoming unavailable" control) test degradation
    without lying about what the spec actually needs. Requesting a field
    that's merely simulated-unavailable is reported exactly like requesting
    one genuinely absent from the schema."""
    log_schema = _load_log_schema()
    log_properties = log_schema["properties"]
    policy_fields = _load_policy_fields()
    unavailable_fields = unavailable_fields or set()

    missing: list[str] = []
    unreliable: list[str] = []
    invalid_values: list[str] = []
    notes: list[str] = []

    # A spec whose behaviourId isn't one of the project's actual known
    # behaviours is unsupported regardless of whether its named fields are
    # individually observable — checked against the real registry
    # (nlp/src/schema_fields.BEHAVIOUR_IDS), not just the "unrecognized:"
    # string-prefix convention one extractor happens to use.
    behaviour_id = spec.get("behaviourId")
    if behaviour_id not in BEHAVIOUR_IDS:
        return ValidationResult(
            status="rejected",
            notes=[f"behaviourId {behaviour_id!r} does not match any of the {len(BEHAVIOUR_IDS)} known, "
                   f"schema-validated behaviour patterns: {sorted(BEHAVIOUR_IDS)}."],
        )

    required_fields = spec.get("requiredFields", [])
    policy_field_list = spec.get("policyFields", [])
    if not required_fields and not policy_field_list:
        return ValidationResult(
            status="rejected",
            notes=["Spec names zero fields — nothing to compile. A supported spec must request at least one "
                   "observable field."],
        )

    # threshold must be a positive WHOLE number (a fractional count of
    # events is meaningless — 0.5 failures cannot happen); timeWindow.amount
    # only needs to be positive (1.5 minutes is a real, convertible
    # duration — spec_bridge.py converts it to a whole number of seconds).
    threshold_block = spec.get("threshold")
    if threshold_block is not None:
        values = [v for v in threshold_block.values() if isinstance(v, (int, float))]
        for n in values:
            if n <= 0:
                invalid_values.append("threshold")
                notes.append(f"'threshold' contains a non-positive value ({n!r}).")
                break
            if not float(n).is_integer():
                invalid_values.append("threshold")
                notes.append(f"'threshold' contains a non-integer value ({n!r}) — a count of events cannot "
                              f"be fractional.")
                break

    time_window_block = spec.get("timeWindow")
    if time_window_block is not None:
        if "amount" not in time_window_block:
            invalid_values.append("timeWindow")
            notes.append("'timeWindow' is missing its 'amount' key — malformed, cannot compile.")
        else:
            amount = time_window_block["amount"]
            if not isinstance(amount, (int, float)):
                invalid_values.append("timeWindow")
                notes.append(f"'timeWindow.amount' ({amount!r}) is not a number.")
            elif amount <= 0:
                invalid_values.append("timeWindow")
                notes.append(f"'timeWindow' contains a non-positive amount ({amount!r}).")

    # Structural dependency check: the recipe a known behaviourId compiles
    # to is fixed (see spec_bridge.RECIPE_TEMPLATES), so the fields it will
    # reference are knowable regardless of what this particular spec
    # happened to request. A field the recipe structurally needs, that was
    # never requested at all, is caught here — separately from the
    # per-field loop below, which only ever checks fields that WERE asked
    # for.
    structural_log_fields, structural_policy_fields = structural_dependencies(behaviour_id)
    requested_log = set(required_fields)
    requested_policy = set(policy_field_list)
    for f in sorted(structural_log_fields - requested_log):
        missing.append(f)
        notes.append(f"'{f}' is required by the {behaviour_id} recipe but was never requested by the spec.")
    for pf in sorted(structural_policy_fields - requested_policy):
        missing.append(pf)
        notes.append(f"'{pf}' is required by the {behaviour_id} recipe but was never requested by the spec.")

    for f in required_fields:
        if f in unavailable_fields:
            missing.append(f)
            notes.append(f"'{f}' is being simulated as unavailable in the schema.")
            continue
        if f not in log_properties:
            missing.append(f)
            notes.append(f"'{f}' is not a field in {LOG_SCHEMA_PATH.name} — cannot compile a rule that reads it.")
        elif log_properties[f].get("observability") == "unreliable":
            unreliable.append(f)
            notes.append(
                f"'{f}' exists in the log schema but is annotated observability=unreliable "
                f"({log_properties[f].get('description', '')}) — a rule must not depend on it."
            )

    for pf in policy_field_list:
        if pf in unavailable_fields:
            missing.append(pf)
            notes.append(f"'{pf}' is being simulated as unavailable in the schema.")
            continue
        bare_name = pf.removeprefix("policy.")
        if bare_name not in policy_fields:
            missing.append(pf)
            notes.append(f"'{pf}' is not a field in {POLICY_SCHEMA_PATH.name} — cannot compile a rule that reads it.")

    status = "rejected" if (missing or unreliable or invalid_values) else "supported"
    if status == "supported":
        notes.append("All requested fields are present and reliably observable.")

    return ValidationResult(status=status, missingFields=sorted(set(missing)), unreliableFields=unreliable,
                             invalidValues=invalid_values, notes=notes)
