"""Compiled spec -> Sigma rule export.

Sigma (https://github.com/SigmaHQ/sigma) is the real, industry-standard
detection rule format. Exporting to it alongside the Scala/Spark rule is
what makes a SENTINEL Forge output usable by SIEMs/tools that speak Sigma,
not just this project's own compiler.

This maps each of the 3 recipes in docs/spec/compiled-spec-format.md as
faithfully as Sigma's real specification allows:

- DistinctCountWithinWindow -> a native Sigma `value_count` correlation
  rule. This is a clean, exact mapping.
- SequenceThenTrigger -> a base rule for the failure event, one Sigma
  `event_count` correlation to apply the threshold, then a
  `temporal_ordered` correlation chaining that count correlation with the
  trigger-event base rule. Sigma's spec supports correlation rules
  referencing other correlation rules by name, which is what makes this
  chain representable at all — still a best-effort composition, not
  something to treat as pre-validated against a real Sigma backend.
- PolicyCompare -> Sigma's base specification has NO mechanism for
  comparing a log field against an external reference/lookup table (only
  field-to-literal or field-to-field-on-the-same-event comparisons via the
  `fieldref` modifier). The generated rule captures the log-observable half
  of the condition (e.g. `mfa_used: false`) and carries an explicit
  `# LIMITATION` comment rather than silently pretending the policy join
  travelled into a format that cannot express it.

Usage:
    python compiler/src/sigma_export.py \
        data/samples/ir/compiled/repeated-failed-login-then-success.compiled.json
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _base_rule(name: str, title: str, event_type: str) -> dict:
    return {
        "title": title,
        "name": name,
        "status": "experimental",
        "author": "SENTINEL Forge (generated)",
        "date": date.today().isoformat(),
        "logsource": {"category": "authentication"},
        "detection": {
            "selection": {"event_type": event_type},
            "condition": "selection",
        },
    }


def export_distinct_count(spec: dict) -> list[dict]:
    base_name = f"{spec['behaviourId']}_base"
    base = _base_rule(base_name, f"{spec['behaviourId']} (base event)", spec["filterEventType"])
    correlation = {
        "title": f"SENTINEL Forge: {spec['behaviourId']}",
        "status": "experimental",
        "author": "SENTINEL Forge (generated)",
        "date": date.today().isoformat(),
        "correlation": {
            "type": "value_count",
            "rules": [base_name],
            "group-by": [spec["groupingKey"]],
            "timespan": f"{spec['timeWindowSeconds']}s",
            "condition": {"field": spec["distinctField"], "gte": spec["distinctThreshold"]},
        },
    }
    return [base, correlation]


def export_sequence_then_trigger(spec: dict) -> list[dict]:
    failures_name = f"{spec['behaviourId']}_failures"
    trigger_name = f"{spec['behaviourId']}_trigger"
    count_corr_name = f"{spec['behaviourId']}_failure_count"

    failures_base = _base_rule(failures_name, f"{spec['behaviourId']} (counted event)", spec["countEventType"])
    trigger_base = _base_rule(trigger_name, f"{spec['behaviourId']} (trigger event)", spec["triggerEventType"])

    count_correlation = {
        "title": f"{spec['behaviourId']} (threshold on counted event)",
        "status": "experimental",
        "author": "SENTINEL Forge (generated)",
        "date": date.today().isoformat(),
        "name": count_corr_name,
        "correlation": {
            "type": "event_count",
            "rules": [failures_name],
            "group-by": [spec["groupingKey"]],
            "timespan": f"{spec['timeWindowSeconds']}s",
            "condition": {"gte": spec["countThreshold"]},
        },
    }

    sequence_correlation = {
        "title": f"SENTINEL Forge: {spec['behaviourId']}",
        "status": "experimental",
        "author": "SENTINEL Forge (generated)",
        "date": date.today().isoformat(),
        "description": (
            f"Best-effort Sigma composition: {spec['countThreshold']}+ "
            f"{spec['countEventType']} events, followed by a "
            f"{spec['triggerEventType']} event, for the same {spec['groupingKey']}, "
            f"within {spec['timeWindowSeconds']}s. Chains a correlation rule as the "
            f"input to another correlation rule (per Sigma's correlation spec) — "
            f"validate against a real Sigma backend before production use."
        ),
        "correlation": {
            "type": "temporal_ordered",
            "rules": [count_corr_name, trigger_name],
            "group-by": [spec["groupingKey"]],
            "timespan": f"{spec['timeWindowSeconds']}s",
        },
    }

    return [failures_base, trigger_base, count_correlation, sequence_correlation]


def export_policy_compare(spec: dict) -> list[dict]:
    rule = _base_rule(f"{spec['behaviourId']}_base", f"SENTINEL Forge: {spec['behaviourId']}", spec["filterEventType"])
    op = spec["comparisonOp"]
    if op == "falseWhenRequired":
        rule["detection"]["selection"][spec["logField"]] = False
    # "notEqual" (B4: auth_method != expected_auth_method) has no literal
    # value to filter on at all — it's inherently a field-vs-reference-data
    # comparison, so the base selection can only narrow to the event type.

    rule["fields"] = [spec["logField"]]
    rule["description"] = (
        f"LIMITATION: Sigma's base specification has no mechanism for comparing "
        f"a log field ('{spec['logField']}') against an external policy/reference "
        f"table ('{spec['policyField']}' in account_policy_reference.json). This rule "
        f"captures only the log-observable half of the real condition. The policy "
        f"comparison itself must be implemented via the target SIEM's own "
        f"lookup-list/enrichment mechanism — this is a genuine gap in Sigma's "
        f"format, not an omission in this export."
    )
    return [rule]


def export(compiled_spec_path: Path) -> list[dict]:
    spec = json.loads(compiled_spec_path.read_text(encoding="utf-8"))
    if spec["recipe"] == "DistinctCountWithinWindow":
        return export_distinct_count(spec)
    if spec["recipe"] == "SequenceThenTrigger":
        return export_sequence_then_trigger(spec)
    if spec["recipe"] == "PolicyCompare":
        return export_policy_compare(spec)
    raise ValueError(f"Unknown recipe: {spec['recipe']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compiled_spec", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    docs = export(args.compiled_spec)
    out_path = args.out or (REPO_ROOT / "spark" / "sigma" / (args.compiled_spec.stem.replace(".compiled", "") + ".sigma.yml"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(yaml.dump_all(docs, sort_keys=False, default_flow_style=False))
    print(f"Wrote {out_path} ({len(docs)} YAML document(s))")


if __name__ == "__main__":
    main()
