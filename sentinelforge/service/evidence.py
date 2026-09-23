"""The evidence record: everything needed to follow one alert back to the report that created its rule.

It answers six questions, each from stored facts - never from a model's say-so:

  why          the passage(s) of the report the rule came from            (quote-verified spans)
  means        the normalised conditions: count + semantics, window       (plain English + values)
  supported    which fields the data provides and which policy it joins   (typed dependency table)
  fired        the supporting events, their times and observed values     (from THIS run's output)
  executed     rule version, hash, compiler version, engine, run id       (so it can be reproduced)
  uncertain    what is not known, not measured, or not verified

This is an *evidence record*, not a proof. The quotes are verified to exist at their offsets in the stored
report (a substring check); that does not establish the passage means what the rule encodes, and the record
says so in its `uncertain` section.
"""
from __future__ import annotations

from .. import behaviours as B
from ..conditions import verify_quote
from ..validation import check_dataset

RECORD_VERSION = "1"


def _fmt_seconds(s) -> str:
    s = int(s)
    for unit, n in (("hour", 3600), ("minute", 60)):
        if s % n == 0 and s >= n:
            k = s // n
            return f"{k} {unit}{'s' if k != 1 else ''} ({s} s)"
    return f"{s} s"


def explain_alert(beh: B.Behaviour, compiled: dict, alert: dict) -> str:
    st = alert.get("status")
    if st == "insufficient_context":
        why = {"no_policy_record": "the account has no record in the policy reference",
               "policy_conflict": "the policy reference holds conflicting records for this account",
               "policy_value_null": "the policy record has no value for the compared field",
               "log_value_null": "the event does not record the compared field (value unobserved)"}.get(alert.get("reason"), "context is missing")
        return (f"No decision: the rule could not evaluate event {alert['triggeringEventId']} because {why}. "
                f"This is reported instead of guessing that the login was compliant.")
    if beh.recipe == B.SEQUENCE_THEN_TRIGGER:
        n = alert.get("matchedCount")
        return (f"Account {alert['groupKey']} had {n} {compiled['countEventType']} events between {alert['windowStart']} and "
                f"{alert['detectedAt']} (window {_fmt_seconds(compiled['timeWindowSeconds'])}, threshold at least {compiled['countThreshold']}), "
                f"then {compiled['triggerEventType']} event {alert['triggeringEventId']} at {alert['detectedAt']}.")
    if beh.recipe == B.DISTINCT_COUNT_WITHIN_WINDOW:
        vals = sorted({e.get("value") for e in alert.get("evidence", []) if e.get("value") is not None})
        shown = ", ".join(map(str, vals[:6])) + (" ..." if len(vals) > 6 else "")
        return (f"{beh.grouping_key} {alert['groupKey']} had {compiled['filterEventType']} events for {alert.get('matchedCount')} distinct "
                f"{beh.distinct_field} values ({shown}) between {alert['windowStart']} and {alert['detectedAt']} "
                f"(window {_fmt_seconds(compiled['timeWindowSeconds'])}, threshold at least {compiled['distinctThreshold']}); "
                f"the count first reached the threshold at event {alert['triggeringEventId']}.")
    if beh.id == "auth-method-policy-violation":
        return (f"Successful login {alert['triggeringEventId']} for {alert['groupKey']} used auth_method '{alert.get('observedValue')}', "
                f"but the policy reference expects '{alert.get('expectedValue')}'.")
    return (f"Successful login {alert['triggeringEventId']} for {alert['groupKey']} had mfa_used = {str(alert.get('observedValue')).lower()}, "
            f"but the policy reference says MFA is required for this account.")


def normalised_meaning(beh: B.Behaviour, compiled: dict) -> dict:
    out = {"behaviour": {"id": beh.id, "name": beh.display_name, "checks": beh.checks, "attack": list(beh.attack)},
           "conditions": []}
    if beh.count_semantics:
        n = compiled.get("countThreshold") or compiled.get("distinctThreshold")
        sem = {"event_count": f"at least {n} {compiled.get('countEventType')} events for the same {beh.grouping_key}",
               "distinct_accounts": f"at least {n} distinct account_id values among {compiled.get('filterEventType')} events from the same {beh.grouping_key}",
               "distinct_hosts": f"at least {n} distinct source_host values among {compiled.get('filterEventType')} events for the same {beh.grouping_key}"}[beh.count_semantics]
        out["conditions"].append({"name": "count", "semantics": beh.count_semantics, "value": n, "comparator": "gte", "plain": sem})
        out["conditions"].append({"name": "window", "seconds": compiled["timeWindowSeconds"], "closed": True,
                                  "plain": f"within {_fmt_seconds(compiled['timeWindowSeconds'])}, both ends inclusive, at microsecond precision"})
        if compiled.get("triggerEventType"):
            out["conditions"].append({"name": "trigger", "plain": f"followed by a {compiled['triggerEventType']} for the same {beh.grouping_key}"})
    else:
        out["conditions"].append({"name": "comparison", "plain": beh.checks})
    return out


def build(*, report: dict, analysis: dict, rule_version: dict, run: dict, dataset_version: dict, alert: dict,
          alert_count_context: dict | None = None) -> dict:
    compiled = rule_version["compiled"]
    beh = B.get(compiled["behaviourId"])
    rec = (analysis.get("result") or {}).get("reconciliation") or {}
    spec = rec.get("spec") or {}
    conds = spec.get("conditions", {})

    passages = []
    for role in ("count", "window"):
        ev = (conds.get(role) or {}).get("evidence")
        if ev:
            passages.append({"role": role, "quote": ev["quote"], "start": ev["start"], "end": ev["end"],
                             "sentenceStart": ev["sentenceStart"], "sentenceEnd": ev["sentenceEnd"],
                             "quoteVerified": verify_quote(report["text"], ev)})
    for sig in (rec.get("conditions") or {}).get("signals", []):
        if sig["behaviourId"] == beh.id:
            for cue in sig["cues"][:3]:
                passages.append({"role": "behaviour cue", "quote": cue["quote"], "start": cue["start"], "end": cue["end"],
                                 "sentenceStart": cue["sentenceStart"], "sentenceEnd": cue["sentenceEnd"],
                                 "quoteVerified": verify_quote(report["text"], cue)})
    overrides = rule_version.get("overrides")

    support = check_dataset(beh.id, dataset_version["profile"])
    counts = (run.get("summary") or {}).get("counts", {})
    uncertain = list(dict.fromkeys(
        (rec.get("uncertainties") or [])
        + ["The quoted passages are quote-verified (they exist at those offsets in the stored report); that does not prove they mean what the rule encodes."]
        + ([f"An analyst changed the rule after extraction ({', '.join(overrides.get('changed', []))}); the changed values are NOT backed by the report."] if overrides else [])
        + ([f"{counts['quarantined']} event row(s) in this dataset could not be evaluated (malformed or missing keys) and were excluded from the run."] if counts.get("quarantined") else [])
        + ([f"{counts['duplicatesDropped']} redelivered event(s) were collapsed to one before evaluation."] if counts.get("duplicatesDropped") else [])
        + ([f"{alert_count_context['insufficient']} result(s) in this run could not be decided for lack of policy/log values."]
           if alert_count_context and alert_count_context.get("insufficient") else [])
        + ([f"Dataset is {dataset_version['kind']} data, not production telemetry."] if dataset_version.get("kind") != "uploaded" else [])))
    return {
        "recordVersion": RECORD_VERSION,
        "alert": {"id": alert["triggeringEventId"], "status": alert["status"], "groupKey": alert["groupKey"],
                  "detectedAt": alert["detectedAt"]},
        "why": {"report": {"id": report["id"], "title": report["title"], "sha256": report["text_sha256"], "source": report["source"]},
                "passages": passages,
                "note": "Quote verification only: each passage exists at its offsets in the stored report."},
        "means": normalised_meaning(beh, compiled),
        "supported": {"dataset": {"id": dataset_version["id"], "name": dataset_version["name"], "kind": dataset_version["kind"],
                                  "version": dataset_version["version"], "fingerprint": dataset_version["fingerprint"]},
                      "dependencies": [d.to_dict() for d in support.dependencies], "allSupported": support.supported},
        "fired": {"explanation": explain_alert(beh, compiled, alert),
                  "supportingEvents": [{"eventId": e["eventId"], "timestamp": e["timestamp"], "value": e.get("value")}
                                       for e in alert.get("evidence", [])],
                  "triggeringEventId": alert["triggeringEventId"], "observedValue": alert.get("observedValue"),
                  "expectedValue": alert.get("expectedValue"), "matchedCount": alert.get("matchedCount"),
                  "windowStart": alert.get("windowStart"), "reason": alert.get("reason")},
        "executed": {"ruleVersionId": rule_version["id"], "ruleVersion": rule_version["version"], "ruleHash": rule_version["rule_hash"],
                     "compilerVersion": rule_version["compiler_version"], "origin": rule_version["origin"],
                     "runId": run["id"], "engine": run["engine"], "startedAt": run.get("started_at"), "finishedAt": run.get("finished_at"),
                     "sparkVersion": (run.get("summary") or {}).get("sparkVersion"), "compiled": compiled},
        "uncertain": {"items": uncertain, "limitations": list(beh.limitations)},
    }
