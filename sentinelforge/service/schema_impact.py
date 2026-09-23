"""Schema-change impact analysis.

Question answered, precisely: given the profile a dataset had and the profile it has now, which stored rule
versions can no longer be evaluated - and WHICH of each rule's conditions is the reason?

Rules are never adapted to the new schema. A rule that grouped by `source_host` does not quietly start
grouping by something else, and a comparison on `mfa_used` does not quietly treat a missing flag as
compliant: the rule is paused with the exact conditions that cannot be evaluated, the unaffected rules keep
running, and resuming requires a revalidation run on the current data (see workflow.resume_rule).
"""
from __future__ import annotations

from .. import behaviours as B
from ..validation import check_dataset


def condition_map(beh: B.Behaviour, compiled: dict | None = None) -> list[dict]:
    """Each condition of the rule and the fields it needs. The wording is what an analyst reads."""
    w = f"{compiled['timeWindowSeconds']} s" if compiled and compiled.get("timeWindowSeconds") else "the window"
    if beh.recipe == B.SEQUENCE_THEN_TRIGGER:
        n = compiled.get("countThreshold") if compiled else "N"
        return [
            {"condition": f"Group events per {beh.grouping_key}", "fields": [beh.grouping_key]},
            {"condition": f"Count {beh.count_event_type} events (need at least {n})", "fields": ["event_type"]},
            {"condition": f"Fire on a following {beh.trigger_event_type} within {w}", "fields": ["event_type", "timestamp"]},
            {"condition": "Cite the supporting events in every alert", "fields": ["event_id"]},
        ]
    if beh.recipe == B.DISTINCT_COUNT_WITHIN_WINDOW:
        n = compiled.get("distinctThreshold") if compiled else "N"
        return [
            {"condition": f"Group events per {beh.grouping_key}", "fields": [beh.grouping_key]},
            {"condition": f"Count distinct {beh.distinct_field} values (need at least {n})", "fields": [beh.distinct_field]},
            {"condition": f"Only {beh.filter_event_type} events", "fields": ["event_type"]},
            {"condition": f"Bound the count by a {w} window", "fields": ["timestamp"]},
            {"condition": "Cite the supporting events in every alert", "fields": ["event_id"]},
        ]
    return [
        {"condition": "Join each event to the account's policy record", "fields": ["account_id", "policy.account_id"]},
        {"condition": f"Only {beh.filter_event_type} events", "fields": ["event_type"]},
        {"condition": f"Compare {beh.log_field} with policy {beh.policy_field}", "fields": [beh.log_field, f"policy.{beh.policy_field}"]},
        {"condition": "Report the time and event of each result", "fields": ["event_id", "timestamp"]},
    ]


def analyse_rule(behaviour_id: str, compiled: dict, profile: dict, unavailable: set | None = None) -> dict:
    beh = B.get(behaviour_id)
    check = check_dataset(behaviour_id, profile, unavailable)
    bad = {d.field: d for d in check.dependencies if d.status != "ok"}
    blocked, evaluable = [], []
    for c in condition_map(beh, compiled):
        why = [{"field": f, "status": bad[f].status, "detail": bad[f].detail or bad[f].status,
                "expectedType": bad[f].expected, "actualType": bad[f].actual} for f in c["fields"] if f in bad]
        (blocked if why else evaluable).append({**c, "because": why} if why else c)
    return {"affected": bool(bad), "blockedConditions": blocked, "evaluableConditions": evaluable,
            "dependencies": [d.to_dict() for d in check.dependencies]}


def summarise(rules: list[dict]) -> dict:
    return {"total": len(rules), "affected": sum(r["affected"] for r in rules), "unaffected": sum(not r["affected"] for r in rules)}
