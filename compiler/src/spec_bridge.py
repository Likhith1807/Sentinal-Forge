"""The missing link an independent review correctly identified (2026-09-16):
nothing converted a Stage-3-validated extraction spec into the CompiledSpec
format Stage 4's Scala compiler actually reads. Every prior Phase 4/5
result (ReplayCheck's 17/17, StreamingCheck, RobustnessCheck,
ThroughputCheck) ran against hand-authored data/samples/ir/compiled/*.json
files — real proof the *compiler* is correct given a valid spec, not proof
that extraction-to-detection works end to end. This module closes that gap
for real.

Design, consistent with the closed-recipe architecture
(docs/spec/compiled-spec-format.md): each of the 5 known behaviours has a
fixed RECIPE SHAPE (which recipe, which grouping key, which event types,
which comparison) — that structure doesn't come from the report, it's
inherent to what the compiler was built to express. What DOES legitimately
come from extraction is the NUMBERS: the threshold and time window. This
bridge fills in the fixed template for a Stage-3-approved behaviourId with
the actual extracted threshold/window, rather than either (a) letting
extraction invent arbitrary query structure (which nothing in this project
does or should do), or (b) silently falling back to the hand-authored
files forever, which is the bug this fixes.

A behaviourId that isn't in this template map, or a spec that never
reached Stage 3's "supported" verdict, cannot produce a compiled spec —
callers must check both before calling build_compiled_spec.
"""
from __future__ import annotations

# The fixed part of each recipe — see docs/spec/compiled-spec-format.md.
# Never derived from a report; inherent to the compiler's 3-recipe design.
RECIPE_TEMPLATES = {
    "repeated-failed-login-then-success": {
        "recipe": "SequenceThenTrigger",
        "groupingKey": "account_id",
        "countEventType": "login_failure",
        "triggerEventType": "login_success",
    },
    "password-spray-across-accounts": {
        "recipe": "DistinctCountWithinWindow",
        "groupingKey": "source_host",
        "distinctField": "account_id",
        "filterEventType": "login_failure",
    },
    "concurrent-sessions-different-hosts": {
        "recipe": "DistinctCountWithinWindow",
        "groupingKey": "account_id",
        "distinctField": "source_host",
        "filterEventType": "login_success",
    },
    "service-account-interactive-auth": {
        "recipe": "PolicyCompare",
        "filterEventType": "login_success",
        "logField": "auth_method",
        "policyField": "expected_auth_method",
        "comparisonOp": "notEqual",
    },
    "mfa-bypass-on-required-account": {
        "recipe": "PolicyCompare",
        "filterEventType": "login_success",
        "logField": "mfa_used",
        "policyField": "mfa_required",
        "comparisonOp": "falseWhenRequired",
    },
}

def structural_dependencies(behaviour_id: str) -> tuple[set, set]:
    """Fields a compiled rule for this behaviourId will structurally
    reference, independent of what any particular extraction happened to
    ask for — since the recipe shape is fixed (RECIPE_TEMPLATES above),
    these are knowable in advance, not derived from report content.
    Returns (log_fields, policy_fields). Used by Stage 3
    (observability_checker.py) to catch a spec that omitted something the
    recipe needs regardless — see that file's round-2 correction note.

    Every recipe filters or compares on event_type; the two windowed
    recipes also need their groupingKey and timestamp; PolicyCompare's
    generated code (RuleCompiler.scala) hardcodes a join on account_id, so
    that's structurally required for it specifically, not just whatever
    logField the comparison happens to use.
    """
    template = RECIPE_TEMPLATES.get(behaviour_id)
    if template is None:
        return set(), set()

    recipe = template["recipe"]
    log_fields = {"event_type"}
    policy_fields: set = set()

    if recipe == "SequenceThenTrigger":
        log_fields |= {template["groupingKey"], "timestamp"}
    elif recipe == "DistinctCountWithinWindow":
        log_fields |= {template["groupingKey"], template["distinctField"], "timestamp"}
    elif recipe == "PolicyCompare":
        log_fields |= {"account_id", template["logField"]}
        policy_fields.add(f"policy.{template['policyField']}")

    return log_fields, policy_fields


ALL_COMPILED_SPEC_KEYS = [
    "behaviourId", "recipe", "groupingKey", "timeWindowSeconds", "countEventType",
    "countThreshold", "triggerEventType", "distinctField", "distinctThreshold",
    "filterEventType", "logField", "policyField", "comparisonOp",
]

_UNIT_TO_SECONDS = {"seconds": 1, "second": 1, "minutes": 60, "minute": 60, "hours": 3600, "hour": 3600}

# The threshold key name(s) each counting behaviour's gold fixtures and
# real extractor output actually use — checked directly against a live
# run of nlp/src/transformer_extractor.py on the 3 held-out counting
# reports (2026-09-17), not assumed from the gold files alone: that run
# showed repeated-failed-login-then-success -> "failureCount" and
# password-spray-across-accounts -> "distinctAccountCount" both matching
# their gold key exactly, but concurrent-sessions-different-hosts came
# back as "loginCount", NOT gold's "successCount" — a reasonable synonym
# for the same "count of qualifying login_success events" concept, not a
# mislabelling. A single hardcoded name per behaviour would have rejected
# that real, correct extraction, so this allows the small set of names
# actually observed to mean each behaviour's threshold rather than one
# fixed string — while still rejecting a name that belongs to a
# DIFFERENT behaviour (e.g. "distinctAccountCount" landing on
# repeated-failed-login-then-success), which is the actual mislabelling
# error this check exists to catch.
EXPECTED_THRESHOLD_KEYS = {
    "repeated-failed-login-then-success": {"failureCount"},
    "password-spray-across-accounts": {"distinctAccountCount"},
    "concurrent-sessions-different-hosts": {"successCount", "loginCount"},
}


class UnbuildableSpecError(Exception):
    pass


def _extract_threshold_value(threshold: dict | None, behaviour_id: str) -> int | None:
    """CORRECTION (independent review, round 2, 2026-09-16): this previously
    did int(v), which silently truncates a fractional count to a
    meaningless value — int(0.5) == 0, a threshold that would fire on
    every event. A count of events cannot be fractional; a non-integer
    value here means the extraction itself is wrong, and that must reject
    loudly, not compile a broken rule quietly.

    CORRECTION (2026-09-17, project owner's correctness standard): this
    previously took "whichever numeric value is there" from the threshold
    dict, regardless of its key name — so a threshold extracted under a
    key name that actually belongs to a DIFFERENT behaviour would
    silently compile as if it were correct. Now requires one of the key
    names actually observed to mean this behaviour's threshold
    (EXPECTED_THRESHOLD_KEYS above)."""
    if not threshold:
        return None
    expected_keys = EXPECTED_THRESHOLD_KEYS.get(behaviour_id)
    if not expected_keys:
        raise UnbuildableSpecError(
            f"{behaviour_id}: no known expected threshold key name(s) for this behaviour — "
            f"cannot validate a threshold was extracted under the right label."
        )
    matching_keys = [k for k in threshold if k in expected_keys]
    if not matching_keys:
        raise UnbuildableSpecError(
            f"{behaviour_id}: expected one of {sorted(expected_keys)!r} as the threshold key, "
            f"extraction gave {sorted(threshold.keys())!r} instead. A numeric value under a "
            f"key name that means a different behaviour's threshold is not proof it means what "
            f"this behaviour needs."
        )
    raw = threshold[matching_keys[0]]
    if not isinstance(raw, (int, float)):
        raise UnbuildableSpecError(f"{behaviour_id}: threshold[{matching_keys[0]!r}] = {raw!r} is not numeric.")
    if not float(raw).is_integer():
        raise UnbuildableSpecError(
            f"{behaviour_id}: threshold value {raw!r} is not a whole number — a count of events "
            f"cannot be fractional. This means the extracted value itself is wrong, not just its "
            f"representation."
        )
    return int(raw)


def _time_window_seconds(time_window: dict | None, behaviour_id: str) -> int | None:
    if not time_window or "amount" not in time_window:
        return None
    amount = time_window["amount"]
    if not isinstance(amount, (int, float)):
        raise UnbuildableSpecError(f"{behaviour_id}: timeWindow.amount {amount!r} is not a number.")
    unit = time_window.get("unit", "seconds")
    multiplier = _UNIT_TO_SECONDS.get(unit)
    if multiplier is None:
        raise UnbuildableSpecError(f"{behaviour_id}: unknown time unit {unit!r} in extracted timeWindow.")
    # CORRECTION (independent review, round 2): this previously did
    # int(amount) * multiplier — truncating the amount BEFORE multiplying,
    # so 1.5 minutes became int(1.5)=1, then 1*60=60 seconds instead of 90.
    # Multiplying first and rounding the result preserves the intended
    # duration; round() (not int()) because 90.0 should stay 90, not be
    # silently floored if floating-point arithmetic gives 89.999999.
    return round(amount * multiplier)


def build_compiled_spec(extraction_spec: dict) -> dict:
    """@param extraction_spec: the output of nlp/src's extractors, already
    confirmed 'supported' by compiler/src/observability_checker.py — this
    function does not re-validate; callers must check stage3 status first.
    @raises UnbuildableSpecError: if the behaviourId has no known recipe
    template, or a recipe that needs a threshold/window didn't get one from
    extraction (rather than silently defaulting to some made-up number).
    """
    behaviour_id = extraction_spec.get("behaviourId")
    template = RECIPE_TEMPLATES.get(behaviour_id)
    if template is None:
        raise UnbuildableSpecError(
            f"No compiled-spec recipe template for behaviourId {behaviour_id!r} — "
            f"known: {sorted(RECIPE_TEMPLATES)}."
        )

    compiled = {key: None for key in ALL_COMPILED_SPEC_KEYS}
    compiled["behaviourId"] = behaviour_id
    compiled.update(template)

    recipe = template["recipe"]
    if recipe == "SequenceThenTrigger":
        count = _extract_threshold_value(extraction_spec.get("threshold"), behaviour_id)
        window = _time_window_seconds(extraction_spec.get("timeWindow"), behaviour_id)
        if count is None or window is None:
            raise UnbuildableSpecError(
                f"{behaviour_id}: SequenceThenTrigger needs a numeric threshold and a time window; "
                f"extraction gave threshold={extraction_spec.get('threshold')!r}, "
                f"timeWindow={extraction_spec.get('timeWindow')!r}."
            )
        compiled["countThreshold"] = count
        compiled["timeWindowSeconds"] = window

    elif recipe == "DistinctCountWithinWindow":
        count = _extract_threshold_value(extraction_spec.get("threshold"), behaviour_id)
        window = _time_window_seconds(extraction_spec.get("timeWindow"), behaviour_id)
        if count is None or window is None:
            raise UnbuildableSpecError(
                f"{behaviour_id}: DistinctCountWithinWindow needs a numeric threshold and a time window; "
                f"extraction gave threshold={extraction_spec.get('threshold')!r}, "
                f"timeWindow={extraction_spec.get('timeWindow')!r}."
            )
        compiled["distinctThreshold"] = count
        compiled["timeWindowSeconds"] = window

    elif recipe == "PolicyCompare":
        pass  # no numeric parameters at all — template is the complete spec

    else:
        raise UnbuildableSpecError(f"Unknown recipe {recipe!r} in template for {behaviour_id!r}.")

    return compiled
