"""Sigma export - only where the rule's meaning survives, and an explicit refusal everywhere else.

An earlier exporter emitted a "best effort" Sigma file for every recipe, with a comment where the meaning was
lost. A rule that reads the same on the page but fires differently in a SIEM is worse than no rule, so this
module has a support matrix instead:

  distinct-count rules (password spray, multi-host authentication)  ->  Sigma `value_count` correlation.
      Event filter, group-by field, distinct field, threshold (>= N) and time span all map one-to-one. What Sigma
      leaves to the backend is DECLARED in the result (`notPreserved`), never implied away.
  repeated failed logins then success                               ->  REFUSED. The count window must end at the
      triggering success; Sigma can chain an `event_count` and a `temporal_ordered` correlation, but their windows
      are independent, so the chained rule is a different detection.
  auth-method / MFA policy comparisons                              ->  REFUSED. They compare a log field with an
      external policy record. Sigma has no lookup, and an export that dropped the policy side would fire on every
      login with `mfa_used: false`, whether or not MFA was required.
"""
from __future__ import annotations

from datetime import date

import yaml

from . import behaviours as B

NOT_PRESERVED_DISTINCT = [
    "Alert cadence: the SENTINEL Forge rule alerts once per incident (when the distinct count first reaches the "
    "threshold after a non-breaching event); Sigma correlations leave alert cadence to the backend.",
    "Window edges: SENTINEL Forge uses a closed interval [t - W, t] at microsecond precision; interval closure and "
    "evaluation granularity are backend-defined in Sigma.",
    "Exactness: SENTINEL Forge counts distinct values exactly; some backends approximate distinct counts.",
    "Ingestion semantics: de-duplication by event_id and quarantine of malformed timestamps happen before the rule "
    "runs and are not expressible in Sigma.",
]

REFUSALS = {
    B.SEQUENCE_THEN_TRIGGER: (
        "Not exportable. The rule needs at least N failures inside a window that ENDS at the triggering success. "
        "Sigma can chain an event_count correlation into a temporal_ordered correlation, but the two windows are "
        "independent, so the chained rule fires under different conditions. Exporting it would silently change the detection."),
    B.POLICY_COMPARE: (
        "Not exportable. The rule compares a log field with an external policy record. Sigma has no lookup or join; "
        "exporting only the log-side half would alert on every login regardless of policy."),
}


def export_rule(rule_version: dict) -> dict:
    compiled = rule_version["compiled"]
    beh = B.get(compiled["behaviourId"])
    base = {"behaviourId": beh.id, "ruleHash": compiled.get("ruleHash"), "ruleVersion": rule_version.get("version")}
    if beh.recipe != B.DISTINCT_COUNT_WITHIN_WINDOW:
        return {**base, "status": "rejected", "reason": REFUSALS[beh.recipe], "sigma": None, "preserved": [], "notPreserved": []}

    name = f"{beh.id.replace('-', '_')}_events"
    stamp = date.today().isoformat()
    events = {"title": f"{beh.display_name} - {compiled['filterEventType']} events", "name": name, "status": "experimental",
              "author": "SENTINEL Forge (generated)", "date": stamp, "logsource": {"category": "authentication"},
              "detection": {"selection": {"event_type": compiled["filterEventType"]}, "condition": "selection"}}
    corr = {"title": f"SENTINEL Forge: {beh.display_name}", "status": "experimental", "author": "SENTINEL Forge (generated)",
            "date": stamp, "description": beh.checks,
            "correlation": {"type": "value_count", "rules": [name], "group-by": [compiled["groupingKey"]],
                            "timespan": f"{compiled['timeWindowSeconds']}s",
                            "condition": {"gte": compiled["distinctThreshold"], "field": compiled["distinctField"]}}}
    text = "---\n".join(yaml.safe_dump(d, sort_keys=False, allow_unicode=True) for d in (events, corr))
    return {**base, "status": "exported", "sigma": text,
            "preserved": [f"event filter: event_type = {compiled['filterEventType']}", f"group by {compiled['groupingKey']}",
                          f"count distinct {compiled['distinctField']} >= {compiled['distinctThreshold']}",
                          f"time span {compiled['timeWindowSeconds']} s"],
            "notPreserved": NOT_PRESERVED_DISTINCT, "reason": None}
