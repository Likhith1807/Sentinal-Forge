"""Independent pure-Python reference detector, used only to cross-check labels.

Shares no code with the Scala compiler (``RuleCompiler.scala``); it is written
from the semantics in ``docs/spec/detection-semantics.md`` and the compiled
specs' parameters. It exists so that the generator's constructed labels are
never validated by the system under test, and so that a dataset's *background*
can be shown to raise no alerts.

Semantics implemented (matching the compiler where documented, and the
*intended* behaviour where the compiler has a known gap):

* Windows are closed on both ends, on timestamps truncated to whole seconds.
* B1: one alert per ``login_success`` whose account has >= threshold
  ``login_failure`` events in ``[t - W, t]``.
* B2 / B3: an alert at the first event of each *episode* in which the distinct
  count reaches the threshold (a new episode starts after a non-breaching
  event). This is the incident-oriented behaviour; the compiler's current
  one-alert-per-group collapse is a documented gap, not something to copy.
* B4 / B5: one result per ``login_success``; a missing policy row is
  ``insufficient_context``.

It is O(events * window) per group and intended for tests and modest
datasets, not for the full-scale benchmark (that differential test is a later
phase).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Iterable

from .params import B1, B2, B3, B4, B5, DetectionParams


def _epoch_seconds(timestamp: str) -> int:
    """Whole-second epoch for ``YYYY-MM-DDTHH:MM:SS[.mmm]Z`` (milliseconds truncated)."""
    return int(dt.datetime.strptime(timestamp[:19], "%Y-%m-%dT%H:%M:%S")
               .replace(tzinfo=dt.timezone.utc).timestamp())


def detect(events: Iterable[dict], policy_records: Iterable[dict], params: DetectionParams) -> list[dict]:
    """Return alerts as ``{behaviourId, groupKey, eventId, status}`` dicts."""
    prepared = [(_epoch_seconds(e["timestamp"]), e) for e in events]
    prepared.sort(key=lambda pair: (pair[0], pair[1]["event_id"]))
    policy = {r["account_id"]: r for r in policy_records}

    alerts: list[dict] = []
    alerts += _b1(prepared, params)
    alerts += _distinct_episodes(prepared, B2, "login_failure", "source_host", "account_id",
                                 params.b2_window_s, params.b2_distinct_threshold)
    alerts += _distinct_episodes(prepared, B3, "login_success", "account_id", "source_host",
                                 params.b3_window_s, params.b3_host_threshold)
    alerts += _policy(prepared, policy)
    return alerts


def _alert(behaviour, group, event, status="alert"):
    return {"behaviourId": behaviour, "groupKey": group, "eventId": event["event_id"], "status": status}


def _b1(prepared, params):
    by_account = defaultdict(list)
    for sec, event in prepared:
        by_account[event["account_id"]].append((sec, event))
    out = []
    for account, rows in by_account.items():
        failure_secs = [s for s, e in rows if e["event_type"] == "login_failure"]
        for sec, event in rows:
            if event["event_type"] != "login_success":
                continue
            in_window = sum(1 for s in failure_secs if sec - params.b1_window_s <= s <= sec)
            if in_window >= params.b1_fail_threshold:
                out.append(_alert(B1, account, event))
    return out


def _distinct_episodes(prepared, behaviour, event_type, group_field, distinct_field, window, threshold):
    groups = defaultdict(list)
    for sec, event in prepared:
        if event["event_type"] == event_type:
            groups[event[group_field]].append((sec, event))
    out = []
    for group, rows in groups.items():
        in_episode = False
        for i, (sec, event) in enumerate(rows):
            distinct = {r[distinct_field] for s, r in rows[: i + 1] if sec - window <= s <= sec}
            # Peers sharing this whole second are inside the closed window regardless of order.
            distinct |= {r[distinct_field] for s, r in rows[i + 1:] if s == sec}
            breach = len(distinct) >= threshold
            if breach and not in_episode:
                out.append(_alert(behaviour, group, event))
            in_episode = breach
    return out


def _policy(prepared, policy):
    out = []
    for _, event in prepared:
        if event["event_type"] != "login_success":
            continue
        row = policy.get(event["account_id"])
        if row is None:
            out.append(_alert(B4, event["account_id"], event, "insufficient_context"))
            out.append(_alert(B5, event["account_id"], event, "insufficient_context"))
            continue
        # A present-but-null log value is unobserved, not compliant: Python's plain `!=`/`is False`
        # would otherwise get this wrong in BOTH directions relative to the intended semantics —
        # `None != "password"` is True (a false alert), and `None is False` is False (a false
        # no_alert) — so each field is checked for None explicitly before comparing it at all,
        # matching RuleCompiler.scala's fix for the same three-valued-logic gap (both
        # comparisonOps; detection-semantics.md case G originally only tested falseWhenRequired).
        if event["auth_method"] is None:
            out.append(_alert(B4, event["account_id"], event, "insufficient_context"))
        elif event["auth_method"] != row["expected_auth_method"]:
            out.append(_alert(B4, event["account_id"], event))
        if event["mfa_used"] is None:
            out.append(_alert(B5, event["account_id"], event, "insufficient_context"))
        elif row["mfa_required"] and event["mfa_used"] is False:
            out.append(_alert(B5, event["account_id"], event))
    return out


def status_for(label: dict, alerts: list[dict]) -> tuple[str, str | None]:
    """Reduce alerts to the label's incident: ``(status, triggerEventId)``."""
    event_ids = set(label["eventIds"])
    mine = [a for a in alerts if a["behaviourId"] == label["behaviourId"] and a["eventId"] in event_ids]
    hits = [a for a in mine if a["status"] == "alert"]
    if hits:
        return "alert", hits[0]["eventId"]
    insufficient = [a for a in mine if a["status"] == "insufficient_context"]
    if insufficient:
        return "insufficient_context", insufficient[0]["eventId"]
    return "no_alert", None
