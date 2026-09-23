"""Strict validation of a specification, and of a specification against a dataset.

Two questions, answered separately because they fail for different reasons:

1. `validate_spec`     - is this specification *well-formed and unambiguous*?  (types, ranges,
                         conflicting keys, precision) - independent of any data.
2. `check_dataset`     - can THIS dataset evaluate every field the rule reads or emits, with
                         the right type?  ("Can our data support it?") This is also what the
                         schema-change analysis re-runs when a column disappears or changes type.

Nothing here trusts its input: a spec can arrive from an LLM, a model checkpoint or an HTTP client.
`True` is not the number 1, `NaN` is not a threshold, `"5"` is not a count, and a spec that
carries a `threshold` under one key and a different `conditions.count` under another is a
contradiction to reject, not a choice to make.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import behaviours as B

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_SCHEMA_PATH = REPO_ROOT / "data" / "samples" / "schema" / "authentication_log_schema.json"
POLICY_SCHEMA_PATH = REPO_ROOT / "data" / "samples" / "schema" / "account_policy_reference.json"

UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}
_UNIT_ALIASES = {"second": "seconds", "minute": "minutes", "hour": "hours", "day": "days"}
MAX_COUNT = 1_000_000
MAX_WINDOW_SECONDS = 7 * 86400

# What each field the rules touch must look like in a dataset. "any" means presence is enough.
EXPECTED_TYPES = {
    "event_id": "string", "timestamp": "timestamp", "account_id": "string", "event_type": "string",
    "source_host": "string", "source_ip": "string", "auth_method": "string", "mfa_used": "boolean",
    "session_id": "string",
    "policy.expected_auth_method": "string", "policy.mfa_required": "boolean", "policy.account_id": "string",
}
FIELD_ROLE = {  # why the compiled rule needs it, shown to the analyst
    "event_id": "output: identifies the supporting event in every alert",
    "timestamp": "read: orders events and bounds the time window; output: alert time",
    "event_type": "read: selects login_failure / login_success events",
    "account_id": "read: grouping or joined against policy",
    "source_host": "read: grouping or distinct-count column",
    "auth_method": "read: compared with the account's expected method",
    "mfa_used": "read: checked when MFA is required",
    "policy.expected_auth_method": "read (policy join): the method the account should use",
    "policy.mfa_required": "read (policy join): whether the account must use MFA",
    "policy.account_id": "read (policy join key): identifies which policy record applies",
}


@dataclass
class Issue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass
class SpecValidation:
    ok: bool
    issues: list = field(default_factory=list)
    behaviourId: str | None = None
    count: dict | None = None            # normalised: {"value", "semantics"}
    windowSeconds: int | None = None
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues], "behaviourId": self.behaviourId,
                "count": self.count, "windowSeconds": self.windowSeconds, "notes": self.notes}


def _is_real_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _number_problem(x, path: str, what: str) -> Issue | None:
    if isinstance(x, bool):
        return Issue("BOOLEAN_AS_NUMBER", path, f"{what} is a boolean ({x!r}); booleans are not numbers.")
    if not isinstance(x, (int, float)):
        return Issue("NOT_A_NUMBER", path, f"{what} must be a number, got {type(x).__name__} ({x!r}).")
    if not math.isfinite(x):
        return Issue("NONFINITE_NUMBER", path, f"{what} is not finite ({x!r}).")
    return None


def validate_spec(spec) -> SpecValidation:
    issues: list[Issue] = []
    if not isinstance(spec, dict):
        return SpecValidation(False, [Issue("MALFORMED_SPEC", "$", f"A specification must be an object, got {type(spec).__name__}.")])

    raw_id = spec.get("behaviourId")
    if not isinstance(raw_id, str):
        return SpecValidation(False, [Issue("UNKNOWN_BEHAVIOUR", "behaviourId", f"behaviourId must be a string, got {raw_id!r}.")])
    bid = B.canonical_id(raw_id)
    beh = B.BEHAVIOURS.get(bid)
    if beh is None:
        return SpecValidation(False, [Issue("UNKNOWN_BEHAVIOUR", "behaviourId",
                                            f"{raw_id!r} is not one of the supported behaviours: {sorted(B.BEHAVIOURS)}.")])
    notes = [f"legacy id {raw_id!r} mapped to {bid!r}"] if bid != raw_id else []

    # -- field lists
    for key in ("requiredFields", "policyFields"):
        val = spec.get(key, [])
        if not isinstance(val, list) or not all(isinstance(f, str) for f in val):
            issues.append(Issue("MALFORMED_BLOCK", key, f"{key} must be a list of field names."))

    # -- count (legacy `threshold` and v2 `conditions.count` must agree if both are present)
    counts: list[tuple[str, int, str | None]] = []       # (where, value, semantics)
    thr = spec.get("threshold", None)
    if thr is not None:
        if not isinstance(thr, dict):
            issues.append(Issue("MALFORMED_BLOCK", "threshold", f"threshold must be an object, got {type(thr).__name__}."))
        elif len(thr) != 1:
            issues.append(Issue("CONFLICTING_KEYS" if len(thr) > 1 else "MALFORMED_BLOCK", "threshold",
                                f"threshold must carry exactly one count, found keys {sorted(thr)}."))
        else:
            key, val = next(iter(thr.items()))
            prob = _number_problem(val, f"threshold.{key}", "threshold value")
            if prob:
                issues.append(prob)
            else:
                sem = beh.threshold_aliases.get(key)
                if sem is None:
                    issues.append(Issue("AMBIGUOUS_COUNT_ALIAS", f"threshold.{key}",
                                        f"\"{key}\" does not say what is counted for {bid}; accepted keys: "
                                        f"{sorted(beh.threshold_aliases) or 'none (this behaviour has no count)'}."))
                counts.append((f"threshold.{key}", val, sem))
    cond = spec.get("conditions")
    if cond is not None and not isinstance(cond, dict):
        issues.append(Issue("MALFORMED_BLOCK", "conditions", "conditions must be an object."))
        cond = None
    if cond and cond.get("count") is not None:
        c = cond["count"]
        if not isinstance(c, dict):
            issues.append(Issue("MALFORMED_BLOCK", "conditions.count", "count must be an object."))
        else:
            prob = _number_problem(c.get("value"), "conditions.count.value", "count value")
            if prob:
                issues.append(prob)
            else:
                sem = c.get("semantics")
                if sem not in B.COUNT_SEMANTICS:
                    issues.append(Issue("AMBIGUOUS_COUNT_SEMANTICS", "conditions.count.semantics",
                                        f"count semantics must be one of {list(B.COUNT_SEMANTICS)}, got {sem!r}."))
                if c.get("comparator", "gte") != "gte":
                    issues.append(Issue("UNSUPPORTED_COMPARATOR", "conditions.count.comparator",
                                        f"only 'gte' is supported, got {c.get('comparator')!r}."))
                counts.append(("conditions.count", c["value"], sem))

    if beh.recipe == B.POLICY_COMPARE:
        if counts:
            issues.append(Issue("NOT_APPLICABLE", counts[0][0], f"{bid} compares a field with policy and takes no count; "
                                                                 f"a supplied threshold means the spec misunderstands the behaviour."))
    else:
        if not counts and not any(i.path.startswith(("threshold", "conditions.count")) for i in issues):
            issues.append(Issue("MISSING_COUNT", "threshold", f"{bid} needs a minimum count."))
        values = {v for _, v, _ in counts if _is_real_number(v)}
        if len(values) > 1:
            issues.append(Issue("CONFLICTING_KEYS", "threshold", f"threshold and conditions.count disagree ({sorted(values)})."))
        sems = {s for _, _, s in counts if s is not None}
        if len(sems) > 1:
            issues.append(Issue("CONFLICTING_KEYS", "threshold", f"count semantics disagree ({sorted(sems)})."))
        for where, v, sem in counts:
            if _is_real_number(v):
                if not float(v).is_integer():
                    issues.append(Issue("FRACTIONAL_COUNT", where, f"a count of events cannot be fractional ({v!r})."))
                elif v < 1:
                    issues.append(Issue("NON_POSITIVE_COUNT", where, f"count must be at least 1, got {v!r}."))
                elif v > MAX_COUNT:
                    issues.append(Issue("COUNT_OUT_OF_RANGE", where, f"count {v!r} exceeds the supported maximum {MAX_COUNT}."))
            if beh.recipe == B.DISTINCT_COUNT_WITHIN_WINDOW and _is_real_number(v) and float(v).is_integer() and 1 <= v < 2:
                issues.append(Issue("COUNT_TOO_SMALL", where, "a distinct-count threshold of 1 is true for every event, so \"once per incident\" is undefined; use at least 2."))
            if sem is not None and beh.count_semantics is not None and sem != beh.count_semantics:
                issues.append(Issue("COUNT_SEMANTICS_MISMATCH", where,
                                    f"{bid} counts {beh.count_semantics}, but the spec's count is {sem}."))

    # -- time window
    windows: list[tuple[str, float, str]] = []
    tw = spec.get("timeWindow", None)
    if tw is not None:
        if not isinstance(tw, dict):
            issues.append(Issue("MALFORMED_BLOCK", "timeWindow", f"timeWindow must be an object, got {type(tw).__name__}."))
        else:
            extra = sorted(set(tw) - {"amount", "unit"})
            if extra:
                issues.append(Issue("UNEXPECTED_KEY", "timeWindow", f"timeWindow has unexpected keys {extra}."))
            if "amount" not in tw:
                issues.append(Issue("MALFORMED_BLOCK", "timeWindow.amount", "timeWindow is missing its amount."))
            else:
                prob = _number_problem(tw["amount"], "timeWindow.amount", "window amount")
                unit = _UNIT_ALIASES.get(tw.get("unit"), tw.get("unit"))
                if prob:
                    issues.append(prob)
                elif unit not in UNIT_SECONDS:
                    issues.append(Issue("UNKNOWN_UNIT", "timeWindow.unit",
                                        f"unit must be one of {sorted(UNIT_SECONDS)}, got {tw.get('unit')!r} - a missing or unknown "
                                        f"unit is never defaulted."))
                else:
                    windows.append(("timeWindow", float(tw["amount"]), unit))
    if cond and cond.get("window") is not None:
        w = cond["window"]
        if not isinstance(w, dict):
            issues.append(Issue("MALFORMED_BLOCK", "conditions.window", "window must be an object."))
        else:
            prob = _number_problem(w.get("amount"), "conditions.window.amount", "window amount")
            unit = _UNIT_ALIASES.get(w.get("unit"), w.get("unit"))
            if prob:
                issues.append(prob)
            elif unit not in UNIT_SECONDS:
                issues.append(Issue("UNKNOWN_UNIT", "conditions.window.unit", f"unknown unit {w.get('unit')!r}."))
            else:
                windows.append(("conditions.window", float(w["amount"]), unit))

    window_seconds = None
    if beh.recipe == B.POLICY_COMPARE:
        if windows:
            issues.append(Issue("NOT_APPLICABLE", windows[0][0], f"{bid} evaluates single events and takes no time window."))
    else:
        if not windows and not any(i.path.startswith(("timeWindow", "conditions.window")) for i in issues):
            issues.append(Issue("MISSING_WINDOW", "timeWindow", f"{bid} needs a time window."))
        secs = set()
        for where, amount, unit in windows:
            s = amount * UNIT_SECONDS[unit]
            if amount <= 0:
                issues.append(Issue("NON_POSITIVE_WINDOW", where, f"window amount must be positive, got {amount!r}."))
            elif abs(s - round(s)) > 1e-9:
                issues.append(Issue("UNSUPPORTED_PRECISION", where,
                                    f"{amount:g} {unit} = {s:g} s is not a whole number of seconds; the compiler's window is whole seconds."))
            elif round(s) < 1 or round(s) > MAX_WINDOW_SECONDS:
                issues.append(Issue("WINDOW_OUT_OF_RANGE", where, f"window must be between 1 s and {MAX_WINDOW_SECONDS} s, got {s:g} s."))
            else:
                secs.add(round(s))
        if len(secs) > 1:
            issues.append(Issue("CONFLICTING_KEYS", "timeWindow", f"timeWindow and conditions.window disagree ({sorted(secs)} s)."))
        elif len(secs) == 1:
            window_seconds = secs.pop()

    count_norm = None
    if beh.recipe != B.POLICY_COMPARE and counts and not issues:
        count_norm = {"value": int(counts[0][1]), "semantics": beh.count_semantics}
    return SpecValidation(ok=not issues, issues=issues, behaviourId=bid, count=count_norm,
                          windowSeconds=window_seconds if not issues else None, notes=notes)


# --------------------------------------------------------------------------------- data support

def _log_schema() -> dict:
    return json.loads(LOG_SCHEMA_PATH.read_text(encoding="utf-8")).get("properties", {})


def _type_ok(expected: str, actual: str | None) -> bool:
    if actual is None:
        return False
    a = actual.lower()
    if a in ("null", "void"):
        return True                                     # present but never observed: rows degrade to insufficient_context
    if expected == "timestamp":
        return a in ("timestamp", "string", "timestamp_ntz", "date-time", "datetime")
    if expected == "string":
        return a in ("string", "str", "varchar", "text")
    if expected == "boolean":
        return a in ("boolean", "bool")
    return True


@dataclass
class Dependency:
    field: str
    role: str
    expected: str
    actual: str | None
    status: str                          # ok | missing | wrong_type | unreliable | simulated_missing
    detail: str = ""

    def to_dict(self) -> dict:
        return {"field": self.field, "role": self.role, "expectedType": self.expected, "actualType": self.actual,
                "status": self.status, "detail": self.detail}


@dataclass
class DatasetCheck:
    supported: bool
    dependencies: list = field(default_factory=list)

    @property
    def blocking(self) -> list:
        return [d for d in self.dependencies if d.status != "ok"]

    def to_dict(self) -> dict:
        return {"supported": self.supported, "dependencies": [d.to_dict() for d in self.dependencies],
                "blocking": [d.to_dict() for d in self.blocking]}


def dataset_profile_from_columns(log_columns: dict, policy_columns: dict | None = None) -> dict:
    """A dataset profile is just {"columns": {name: type}, "policyColumns": {name: type}}."""
    return {"columns": {k: str(v).lower() for k, v in log_columns.items()},
            "policyColumns": {k: str(v).lower() for k, v in (policy_columns or {}).items()}}


def check_dataset(behaviour_id: str, profile: dict, unavailable: set | None = None) -> DatasetCheck:
    """Every field the compiled rule reads or emits must exist in `profile` with the right type."""
    beh = B.get(behaviour_id)
    if beh is None:
        return DatasetCheck(False, [])
    unavailable = unavailable or set()
    schema = _log_schema()
    deps: list[Dependency] = []
    policy_deps = beh.policy_fields + (("policy.account_id",) if beh.recipe == B.POLICY_COMPARE else ())
    for f in beh.log_fields + policy_deps:
        is_policy = f.startswith("policy.")
        bare = f.removeprefix("policy.")
        cols = profile.get("policyColumns" if is_policy else "columns", {})
        expected = EXPECTED_TYPES[f]
        actual = cols.get(bare)
        role = FIELD_ROLE.get(f, "read")
        if f in unavailable or bare in unavailable and not is_policy:
            deps.append(Dependency(f, role, expected, None, "simulated_missing", "treated as removed from the data source"))
        elif actual is None:
            deps.append(Dependency(f, role, expected, None, "missing",
                                   "no such column in the policy reference" if is_policy else "no such column in the event data"))
        elif not _type_ok(expected, actual):
            deps.append(Dependency(f, role, expected, actual, "wrong_type",
                                   f"the rule compares this field as {expected}; the data provides {actual}"))
        elif not is_policy and schema.get(bare, {}).get("observability") == "unreliable":
            deps.append(Dependency(f, role, expected, actual, "unreliable", "documented as unreliable in the log schema"))
        else:
            deps.append(Dependency(f, role, expected, actual, "ok"))
    return DatasetCheck(supported=all(d.status == "ok" for d in deps), dependencies=deps)


def default_profile() -> dict:
    """The documented schema as a dataset profile: what a conforming data source provides."""
    props = json.loads(LOG_SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]

    def kind(p: dict) -> str:
        t = p.get("type")
        t = [x for x in (t if isinstance(t, list) else [t]) if x != "null"][0]
        return "timestamp" if p.get("format") == "date-time" else t

    policy = json.loads(POLICY_SCHEMA_PATH.read_text(encoding="utf-8"))["records"][0]
    pol_cols = {k: ("boolean" if isinstance(v, bool) else "string") for k, v in policy.items() if k != "account_type"}
    return dataset_profile_from_columns({k: kind(p) for k, p in props.items()}, pol_cols)
