"""Independent-reference differential/property-based testing (roadmap item 13).

Generates random, bounded event scenarios with Hypothesis, runs each behaviour's whole batch
through the REAL Scala compiler (`DifferentialCheck.scala`, one JVM invocation per batch) and
independently through `scripts/datagen/refdetect.py` (which shares no code with the Scala
compiler — written from `docs/spec/detection-semantics.md`'s semantics), and asserts the two
agree on every scenario.

Each scenario gets globally-unique account_id/source_host values (the same isolation trick
`scripts/datagen/incidents.py` uses for labelled incidents), so many scenarios can be unioned into
one big event list and compiled ONCE per behaviour per test — batching is what makes "thousands of
random cases" practical without paying a ~10-15s JVM startup cost per case. `--n-scenarios` and
`--max-examples` control the total: default 200 scenarios/example x 5 examples x 5 behaviours =
5,000 scenarios per run.

Usage:
    python compiler/test/differential_property_test.py
    python compiler/test/differential_property_test.py --n-scenarios 500 --max-examples 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.datagen.params import B1, B2, B3, B4, B5, DetectionParams  # noqa: E402
from scripts.datagen.refdetect import detect, status_for  # noqa: E402
from scripts.datagen.policy import AUTH_METHODS  # noqa: E402

PARAMS = DetectionParams.from_compiled_specs()
ANCHOR = int(dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc).timestamp())
SBT = "C:/Users/likhi/AppData/Local/Coursier/data/bin/sbt.bat"
JAVA_HOME = (r"C:\Users\likhi\AppData\Local\Coursier\cache\arc\https\github.com\adoptium\temurin17-binaries"
            r"\releases\download\jdk-17.0.20.1%252B1\OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip"
            r"\jdk-17.0.20.1+1")


def iso(offset_s: int) -> str:
    return dt.datetime.fromtimestamp(ANCHOR + offset_s, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


class Counter:
    """Process-wide-unique integer ids, so event_ids never collide across scenarios in a batch."""
    def __init__(self):
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


# --------------------------------------------------------------------------- Hypothesis strategies
_window_offset = lambda hi: st.integers(min_value=0, max_value=hi)  # noqa: E731


@st.composite
def b1_scenario(draw, sid: int, counter: Counter):
    w = PARAMS.b1_window_s
    fail_offsets = draw(st.lists(_window_offset(4 * w), unique=True, max_size=10))
    has_success = draw(st.booleans())
    success_offset = draw(_window_offset(4 * w)) if has_success else None
    acct, host = f"s{sid}-acct", f"s{sid}-host"
    events = [_ev(counter, acct, "login_failure", host, iso(o)) for o in fail_offsets]
    if has_success:
        events.append(_ev(counter, acct, "login_success", host, iso(success_offset)))
    return _ensure_nonempty({"events": events, "policy": [], "groupKeys": [acct]}, counter)


@st.composite
def distinct_count_scenario(draw, sid: int, counter: Counter, window_s: int, group_prefix: str,
                            distinct_prefix: str, event_type: str, max_events: int, max_distinct: int):
    n = draw(st.integers(min_value=0, max_value=max_events))
    offsets = draw(st.lists(_window_offset(4 * window_s), min_size=n, max_size=n))
    distinct_indices = draw(st.lists(st.integers(min_value=0, max_value=max_distinct - 1), min_size=n, max_size=n))
    group = f"s{sid}-{group_prefix}"
    events = []
    for offset, idx in zip(offsets, distinct_indices):
        distinct_val = f"s{sid}-{distinct_prefix}{idx}"
        acct = distinct_val if distinct_prefix == "acct" else group
        host = distinct_val if distinct_prefix == "host" else group
        events.append(_ev(counter, acct, event_type, host, iso(offset)))
    return _ensure_nonempty({"events": events, "policy": [], "groupKeys": [group]}, counter)


@st.composite
def policy_scenario(draw, sid: int, counter: Counter, log_field: str):
    acct = f"s{sid}-acct"
    # Same empty-array schema-inference hazard as _ensure_nonempty guards for events (Hypothesis's
    # shrinker found it immediately: minimizing every scenario's has_policy to False collapses the
    # WHOLE batch's "policy" array to empty, which Spark can't infer a struct type for). Scenario 0
    # always contributes a real policy row so the batch-level array is never empty, regardless of
    # what every other scenario draws.
    has_policy = True if sid == 0 else draw(st.booleans())
    policy = []
    if has_policy:
        if log_field == "auth_method":
            policy.append({"account_id": acct, "expected_auth_method": draw(st.sampled_from(AUTH_METHODS)),
                          "mfa_required": None})
        else:
            policy.append({"account_id": acct, "expected_auth_method": None,
                          "mfa_required": draw(st.booleans())})
    n_events = draw(st.integers(min_value=1, max_value=3))
    events = []
    for i in range(n_events):
        event_type = draw(st.sampled_from(["login_success", "login_failure"]))
        auth_method = draw(st.one_of(st.none(), st.sampled_from(AUTH_METHODS))) if log_field == "auth_method" else "password"
        mfa_used = draw(st.one_of(st.none(), st.booleans())) if log_field == "mfa_used" else False
        events.append(_ev(counter, acct, event_type, f"s{sid}-host", iso(i), auth_method, mfa_used))
    return _ensure_nonempty({"events": events, "policy": policy, "groupKeys": [acct]}, counter)


def _ev(counter: Counter, account_id, event_type, source_host, timestamp, auth_method=None, mfa_used=None) -> dict:
    return {"event_id": f"e{counter.next():08d}", "timestamp": timestamp, "account_id": account_id,
            "event_type": event_type, "source_host": source_host, "auth_method": auth_method, "mfa_used": mfa_used}


def _ensure_nonempty(scenario: dict, counter: Counter) -> dict:
    """If EVERY scenario in a batch has zero events, Spark can't infer a struct type for the
    exploded (always-empty) 'events' array and the whole batch fails before any comparison runs —
    Hypothesis's shrinker found this immediately (all-empty-events is the natural minimal case for
    every strategy here). A harmless anchor event, far outside any window and using no distinct
    value any real assertion depends on, guarantees a non-empty, inferrable schema without changing
    what the scenario is actually testing.
    """
    if not scenario["events"]:
        # Both fields (not just the groupKey) use the scenario's own key: B2 filters on
        # login_failure too, so a shared literal host across scenarios would let two otherwise-
        # empty scenarios silently merge into one artificial group instead of staying isolated.
        anchor = scenario["groupKeys"][0] if scenario["groupKeys"] else f"s-anchor-{counter.next()}"
        scenario["events"].append(_ev(counter, anchor, "login_failure", anchor, iso(10 ** 7)))
    return scenario


# --------------------------------------------------------------------------- batch runner
def run_batch(scenarios_by_behaviour: dict[str, list[dict]], tmp_dir: Path) -> dict[str, list[dict]]:
    """One JVM invocation: {behaviourId: unioned {events, policy}} in, {behaviourId: [alert rows]} out."""
    batch_in = {}
    for behaviour_id, scenarios in scenarios_by_behaviour.items():
        batch_in[behaviour_id] = {
            "events": [e for s in scenarios for e in s["events"]],
            "policy": [p for s in scenarios for p in s["policy"]],
        }
    in_path, out_path = tmp_dir / "differential_in.json", tmp_dir / "differential_out.json"
    in_path.write_text(json.dumps(batch_in), encoding="utf-8")

    cmd = [SBT, "-Dsf.heap=2g", f'runMain sentinelforge.compiler.DifferentialCheck {in_path} {out_path}']
    import os
    env = dict(os.environ, JAVA_HOME=JAVA_HOME)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"DifferentialCheck failed:\nSTDOUT:\n{proc.stdout[-15000:]}\nSTDERR:\n{proc.stderr[-2000:]}")
    return json.loads(out_path.read_text(encoding="utf-8"))


def compare_scenario(behaviour_id: str, scenario: dict, scala_alerts: list[dict], params: DetectionParams) -> tuple[bool, str]:
    """Reduce both systems' output to this scenario's own events, using status_for's reduction rule."""
    event_ids = {e["event_id"] for e in scenario["events"]}
    scala_mine = [a for a in scala_alerts if a["triggeringEventId"] in event_ids]
    scala_alert_ids = {a["triggeringEventId"] for a in scala_mine if a["status"] == "alert"}
    scala_insuff_ids = {a["triggeringEventId"] for a in scala_mine if a["status"] == "insufficient_context"}

    py_alerts = detect(scenario["events"], scenario["policy"], params)
    py_mine = [a for a in py_alerts if a["behaviourId"] == behaviour_id and a["eventId"] in event_ids]
    py_alert_ids = {a["eventId"] for a in py_mine if a["status"] == "alert"}
    py_insuff_ids = {a["eventId"] for a in py_mine if a["status"] == "insufficient_context"}

    if behaviour_id in (B4, B5):
        # PolicyCompare: compare the full per-event status set (alert / insufficient_context / no_alert-by-absence).
        ok = scala_alert_ids == py_alert_ids and scala_insuff_ids == py_insuff_ids
        return ok, f"scala alert={scala_alert_ids} insuff={scala_insuff_ids} | py alert={py_alert_ids} insuff={py_insuff_ids}"
    ok = scala_alert_ids == py_alert_ids
    return ok, f"scala alert={scala_alert_ids} | py alert={py_alert_ids}"


def make_test(behaviour_id: str, strategy_fn, n_scenarios: int, max_examples: int, tmp_dir: Path):
    @settings(max_examples=max_examples, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    @given(data=st.data())
    def test(data):
        scenarios = [data.draw(strategy_fn(sid), label=f"scenario-{sid}") for sid in range(n_scenarios)]
        out = run_batch({behaviour_id: scenarios}, tmp_dir)
        alerts = out[behaviour_id]
        failures = []
        for i, scenario in enumerate(scenarios):
            ok, detail = compare_scenario(behaviour_id, scenario, alerts, PARAMS)
            if not ok:
                failures.append((i, scenario, detail))
        assert not failures, f"{len(failures)}/{len(scenarios)} scenarios disagreed; first: {failures[0]}"
    test.__name__ = f"test_differential_{behaviour_id.replace('-', '_')}"
    return test


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n-scenarios", type=int, default=200)
    ap.add_argument("--max-examples", type=int, default=5)
    ap.add_argument("--tmp-dir", default=None)
    args = ap.parse_args(argv)

    import tempfile
    tmp_dir = Path(args.tmp_dir) if args.tmp_dir else Path(tempfile.mkdtemp(prefix="sf_differential_"))
    tmp_dir.mkdir(parents=True, exist_ok=True)

    counter = Counter()
    strategies = {
        B1: lambda sid: b1_scenario(sid, counter),
        B2: lambda sid: distinct_count_scenario(sid, counter, PARAMS.b2_window_s, "host", "acct",
                                               "login_failure", 12, 7),
        B3: lambda sid: distinct_count_scenario(sid, counter, PARAMS.b3_window_s, "acct", "host",
                                               "login_success", 8, 5),
        B4: lambda sid: policy_scenario(sid, counter, "auth_method"),
        B5: lambda sid: policy_scenario(sid, counter, "mfa_used"),
    }

    total_scenarios, total_failed = 0, []
    for behaviour_id, strategy_fn in strategies.items():
        print(f"=== {behaviour_id}: {args.n_scenarios} scenarios x {args.max_examples} examples ===")
        test = make_test(behaviour_id, strategy_fn, args.n_scenarios, args.max_examples, tmp_dir)
        try:
            test()
            print(f"  OK ({args.n_scenarios * args.max_examples} scenarios agreed)")
            total_scenarios += args.n_scenarios * args.max_examples
        except Exception as exc:  # noqa: BLE001 - an infrastructure failure on one behaviour must not hide the other 4 behaviours' real results
            print(f"  FAIL: {exc}")
            total_failed.append(behaviour_id)

    print(f"\n{len(strategies) - len(total_failed)}/{len(strategies)} behaviours fully agreed "
         f"across ~{total_scenarios} scenarios.")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
