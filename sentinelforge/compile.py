"""Verified specification -> compiled rule (the flat JSON the Scala/Spark compiler executes).

A compiled rule is a pure function of (behaviour recipe, validated count, validated window). Its
`ruleHash` is a content hash of exactly those functional fields, so two runs that claim the same
rule can be checked for it, and a change to any condition - even one digit - changes the hash.
"""
from __future__ import annotations

import hashlib
import json

from . import behaviours as B
from .validation import Issue, validate_spec

COMPILER_VERSION = "2.0.0"
SPEC_VERSION = "2"

ALL_COMPILED_SPEC_KEYS = [
    "behaviourId", "recipe", "groupingKey", "timeWindowSeconds", "countEventType",
    "countThreshold", "triggerEventType", "distinctField", "distinctThreshold",
    "filterEventType", "logField", "policyField", "comparisonOp",
]

# Event-processing policies the executors implement (see docs/spec/detection-semantics.md). They are part
# of the compiled rule so a run can state exactly which semantics it applied.
PROCESSING_POLICY = {
    "timestampPrecision": "millisecond",
    "duplicateEvents": "keep-first-by-event_id",
    "malformedTimestamp": "quarantine-and-count",
    "policyConflict": "insufficient_context",
}


class RuleBuildError(Exception):
    def __init__(self, message: str, issues: list[Issue] | None = None):
        super().__init__(message)
        self.issues = issues or []


def rule_hash(compiled: dict) -> str:
    functional = {k: compiled.get(k) for k in ALL_COMPILED_SPEC_KEYS}
    blob = json.dumps(functional, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def compile_spec(spec: dict) -> dict:
    """Validate strictly, then fill the behaviour's fixed recipe with the spec's own numbers.

    @raises RuleBuildError: with the machine-readable issues, if anything about the spec is malformed,
    ambiguous, conflicting or out of range. It never guesses a missing number and never defaults a unit.
    """
    v = validate_spec(spec)
    if not v.ok:
        raise RuleBuildError("; ".join(f"{i.code} at {i.path}: {i.message}" for i in v.issues), v.issues)
    beh = B.BEHAVIOURS[v.behaviourId]

    compiled: dict = {k: None for k in ALL_COMPILED_SPEC_KEYS}
    compiled.update({
        "behaviourId": beh.id, "recipe": beh.recipe, "groupingKey": beh.grouping_key,
        "countEventType": beh.count_event_type, "triggerEventType": beh.trigger_event_type,
        "distinctField": beh.distinct_field, "filterEventType": beh.filter_event_type,
        "logField": beh.log_field, "policyField": beh.policy_field, "comparisonOp": beh.comparison_op,
    })
    if beh.recipe == B.SEQUENCE_THEN_TRIGGER:
        compiled["countThreshold"] = v.count["value"]
        compiled["timeWindowSeconds"] = v.windowSeconds
    elif beh.recipe == B.DISTINCT_COUNT_WITHIN_WINDOW:
        compiled["distinctThreshold"] = v.count["value"]
        compiled["timeWindowSeconds"] = v.windowSeconds

    compiled["specVersion"] = SPEC_VERSION
    compiled["compilerVersion"] = COMPILER_VERSION
    compiled["countSemantics"] = beh.count_semantics
    compiled["processing"] = dict(PROCESSING_POLICY)
    compiled["ruleHash"] = rule_hash(compiled)
    return compiled
