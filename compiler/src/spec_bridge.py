"""Extraction spec -> compiled rule, as a thin, stable entry point over `sentinelforge`.

History (kept because the reasons still matter): an independent review found that nothing converted a
real NLP extraction into the CompiledSpec format the Scala compiler reads; this module was the bridge.
A later audit of the fine-tuned pipeline (27 of 40 supported reports compiled to the right rule, 7 of 44
reports silently compiled to a wrong or unsupported one) showed the bridge itself was too trusting:

* it accepted `loginCount` as a synonym for "distinct hosts", although the same words could mean an
  event count - an ambiguous alias now rejected;
* it defaulted a missing unit to seconds, and let `True` pass for the number 1;
* it took whatever number an extractor gave it without asking where in the report that number came from.

All of that logic now lives in `sentinelforge.validation` / `sentinelforge.compile` (strict, tested,
single source of truth: `sentinelforge.behaviours`). What remains here is compatibility: the same
function names the harnesses, dashboard and older tests import.

Callers must still run Stage 3 (`observability_checker.validate`) and, for a report, `sentinelforge.reconcile`
first; `build_compiled_spec` validates the spec's own numbers but cannot know whether the report said them.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sentinelforge import behaviours as _B  # noqa: E402
from sentinelforge.compile import ALL_COMPILED_SPEC_KEYS, RuleBuildError, compile_spec  # noqa: E402

UnbuildableSpecError = RuleBuildError

# Derived from the registry, never maintained by hand: the fixed part of each recipe.
RECIPE_TEMPLATES = {
    b.id: {k: v for k, v in {
        "recipe": b.recipe, "groupingKey": b.grouping_key, "countEventType": b.count_event_type,
        "triggerEventType": b.trigger_event_type, "distinctField": b.distinct_field,
        "filterEventType": b.filter_event_type, "logField": b.log_field, "policyField": b.policy_field,
        "comparisonOp": b.comparison_op}.items() if v is not None}
    for b in _B.BEHAVIOURS.values()
}
EXPECTED_THRESHOLD_KEYS = {b.id: set(b.threshold_aliases) for b in _B.BEHAVIOURS.values() if b.threshold_aliases}


def structural_dependencies(behaviour_id: str) -> tuple[set, set]:
    """(log_fields, policy_fields) the compiled rule for this behaviour reads or emits. Fixed by the recipe."""
    beh = _B.get(behaviour_id)
    if beh is None:
        return set(), set()
    return set(beh.log_fields), set(beh.policy_fields)


def build_compiled_spec(extraction_spec: dict) -> dict:
    """@raises UnbuildableSpecError: on any malformed, ambiguous, conflicting or out-of-range spec."""
    return compile_spec(extraction_spec)
