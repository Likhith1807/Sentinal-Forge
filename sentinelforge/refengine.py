"""Independent pure-Python executor of a compiled rule.

Written from the semantics in docs/spec/detection-semantics.md - NOT by transliterating
`RuleCompiler.scala`. It shares no code and no data structures with the Spark implementation, and it is
deliberately naive (sorted lists, nested loops) so that its correctness is checkable by reading it.

Its job is to be the second opinion in the differential tests (alert counts, alert instants, window
boundaries, matched counts, supporting-event evidence) and the oracle for metamorphic tests. Agreement of
two implementations only proves they match each other; the golden scenarios in tests/core hold
hand-computed expectations so a defect SHARED by both still fails a test.

Semantics implemented (v2):
  * timestamps: RFC 3339 with an explicit zone, 0-6 fractional digits, exact integer microseconds.
    Anything else is quarantined ("malformed_timestamp"); a missing one is "null_timestamp".
  * quarantine also covers a null event_id and a null grouping / distinct / join key ("null_<column>").
  * rows sharing an event_id collapse to the earliest instant, ties broken by the smallest row content
    (other columns sorted by name, NULLs skipped, joined by U+0001).
  * window: closed interval [t - W, t] in microseconds; events with an identical timestamp are all inside.
  * SequenceThenTrigger: one alert per trigger event whose group has >= N counted events in the window.
  * DistinctCountWithinWindow: one alert per *incident* - the first event (ordered by timestamp, event_id) at
    which the exact distinct count reaches N after a non-breaching event (a "rising edge").
  * PolicyCompare: one result per matching event; policy rows are deduplicated per account (identical rows
    collapse, conflicting rows make the account `insufficient_context`/`policy_conflict`).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field

_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$")
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def parse_micros(value) -> int | None:
    """Exact integer microseconds since the epoch, or None if `value` is not a valid RFC 3339 instant."""
    if not isinstance(value, str):
        return None
    m = _TS.match(value)
    if not m:
        return None
    y, mo, d, h, mi, s, frac, zone = m.groups()
    try:
        offset = dt.timezone.utc if zone == "Z" else dt.timezone(
            (1 if zone[0] == "+" else -1) * dt.timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
        base = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=offset)
    except ValueError:
        return None
    delta = base - _EPOCH
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000
    return micros + int((frac or "").ljust(6, "0")) if frac else micros


def render_micros(micros: int) -> str:
    """`yyyy-MM-ddTHH:mm:ss.SSSZ` (UTC, milliseconds - the compiler's output format; sub-ms digits truncated)."""
    secs, us = divmod(micros, 1_000_000)
    t = _EPOCH + dt.timedelta(seconds=secs)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + f".{us // 1000:03d}Z"


@dataclass
class RefResult:
    alerts: list = field(default_factory=list)        # status alert / insufficient_context (no_alert omitted)
    all_results: list = field(default_factory=list)   # PolicyCompare: every result incl. no_alert
    quarantine: list = field(default_factory=list)    # [(event_id, reason)]
    stats: dict = field(default_factory=dict)


def _row_content(row: dict) -> str:
    parts = []
    for k in sorted(row):
        if k in ("_micros",):
            continue
        v = row[k]
        if v is None:
            continue
        parts.append("true" if v is True else "false" if v is False else str(v))
    return "\u0001".join(parts)


def _prepare(events: list[dict], key_cols: list[str]) -> tuple[list[dict], list, dict]:
    quarantine: list = []
    ok: list[dict] = []
    for e in events:
        micros = parse_micros(e.get("timestamp"))
        reason = None
        if e.get("event_id") is None:
            reason = "null_event_id"
        elif e.get("timestamp") is None:
            reason = "null_timestamp"
        elif micros is None:
            reason = "malformed_timestamp"
        else:
            for k in key_cols:
                if e.get(k) is None:
                    reason = f"null_{k}"
                    break
        if reason:
            quarantine.append((e.get("event_id"), reason))
            continue
        ok.append({**e, "_micros": micros})
    by_id: dict = defaultdict(list)
    for e in ok:
        by_id[e["event_id"]].append(e)
    clean = [min(rows, key=lambda r: (r["_micros"], _row_content(r))) for rows in by_id.values()]
    by_reason: dict = defaultdict(int)
    for _, reason in quarantine:
        by_reason[reason] += 1
    stats = {"eventsRead": len(events), "eventsEvaluated": len(clean), "quarantined": len(events) - len(ok),
             "duplicatesDropped": len(ok) - len(clean), "quarantineByReason": dict(by_reason)}
    return clean, quarantine, stats


def run_reference(compiled: dict, events: list[dict], policy: list[dict] | None = None) -> RefResult:
    recipe = compiled["recipe"]
    if recipe == "SequenceThenTrigger":
        return _sequence(compiled, events)
    if recipe == "DistinctCountWithinWindow":
        return _distinct(compiled, events)
    if recipe == "PolicyCompare":
        return _policy(compiled, events, policy or [])
    raise ValueError(f"unknown recipe {recipe!r}")


def _sequence(c: dict, events: list[dict]) -> RefResult:
    gk, w = c["groupingKey"], c["timeWindowSeconds"] * 1_000_000
    clean, quarantine, stats = _prepare(events, [gk])
    groups: dict = defaultdict(list)
    for e in clean:
        groups[e[gk]].append(e)
    res = RefResult(quarantine=quarantine, stats=stats)
    for key, rows in groups.items():
        rows.sort(key=lambda r: (r["_micros"], r["event_id"]))
        failures = [r for r in rows if r["event_type"] == c["countEventType"]]
        for trig in (r for r in rows if r["event_type"] == c["triggerEventType"]):
            inside = [f for f in failures if trig["_micros"] - w <= f["_micros"] <= trig["_micros"]]
            if len(inside) >= c["countThreshold"]:
                res.alerts.append({
                    "behaviourId": c["behaviourId"], "groupKey": key, "triggeringEventId": trig["event_id"],
                    "detectedAt": trig["timestamp"], "windowStart": render_micros(trig["_micros"] - w),
                    "matchedCount": len(inside), "status": "alert",
                    "evidence": [f["event_id"] for f in inside]})
    res.alerts.sort(key=lambda a: (a["detectedAt"], a["triggeringEventId"]))
    return res


def _distinct(c: dict, events: list[dict]) -> RefResult:
    gk, dc, w = c["groupingKey"], c["distinctField"], c["timeWindowSeconds"] * 1_000_000
    clean, quarantine, stats = _prepare(events, [gk, dc])
    groups: dict = defaultdict(list)
    for e in clean:
        if e["event_type"] == c["filterEventType"]:
            groups[e[gk]].append(e)
    res = RefResult(quarantine=quarantine, stats=stats)
    for key, rows in groups.items():
        rows.sort(key=lambda r: (r["_micros"], r["event_id"]))
        previously = False
        for r in rows:
            inside = [x for x in rows if r["_micros"] - w <= x["_micros"] <= r["_micros"]]
            distinct = {x[dc] for x in inside}
            breaching = len(distinct) >= c["distinctThreshold"]
            if breaching and not previously:
                res.alerts.append({
                    "behaviourId": c["behaviourId"], "groupKey": key, "triggeringEventId": r["event_id"],
                    "detectedAt": r["timestamp"], "windowStart": render_micros(r["_micros"] - w),
                    "matchedCount": len(distinct), "status": "alert",
                    "evidence": [x["event_id"] for x in inside]})
            previously = breaching
    res.alerts.sort(key=lambda a: (a["detectedAt"], a["triggeringEventId"]))
    return res


def _policy(c: dict, events: list[dict], policy: list[dict]) -> RefResult:
    log_f, pol_f, op = c["logField"], c["policyField"], c["comparisonOp"]
    clean, quarantine, stats = _prepare(events, ["account_id"])
    by_acct: dict = defaultdict(list)
    for p in policy:
        if p.get("account_id") is not None:
            by_acct[p["account_id"]].append(p.get(pol_f))
    res = RefResult(quarantine=quarantine, stats=stats)
    for e in sorted((x for x in clean if x["event_type"] == c["filterEventType"]),
                    key=lambda r: (r["_micros"], r["event_id"])):
        vals = by_acct.get(e["account_id"])
        observed = e.get(log_f)
        expected, status, reason = None, None, None
        if vals is None:
            status, reason = "insufficient_context", "no_policy_record"
        else:
            non_null = {v for v in vals if v is not None}
            has_null = any(v is None for v in vals)
            if len(non_null) + (1 if has_null else 0) > 1:
                status, reason = "insufficient_context", "policy_conflict"
            elif not non_null:
                status, reason = "insufficient_context", "policy_value_null"
            else:
                expected = next(iter(non_null))
                if observed is None:
                    status, reason = "insufficient_context", "log_value_null"
                else:
                    violated = (observed != expected) if op == "notEqual" else (expected is True and observed is False)
                    status, reason = ("alert", "policy_violated") if violated else ("no_alert", "compliant")
        out = {"behaviourId": c["behaviourId"], "groupKey": e["account_id"], "triggeringEventId": e["event_id"],
               "detectedAt": e["timestamp"], "observedValue": observed, "expectedValue": expected,
               "status": status, "reason": reason}
        res.all_results.append(out)
        if status != "no_alert":
            res.alerts.append(out)
    return res
