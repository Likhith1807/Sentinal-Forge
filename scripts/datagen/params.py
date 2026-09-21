"""Detection parameters, read from the compiled specs rather than copied.

The generator builds incidents that sit exactly on either side of each
rule's threshold and window boundary. If those numbers were duplicated here
they could silently drift from what the compiler actually enforces, so they
are loaded from ``data/samples/ir/compiled/*.compiled.json`` and the shape
assumptions the incident builders rely on are asserted up front.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPILED_DIR = REPO_ROOT / "data" / "samples" / "ir" / "compiled"

B1 = "repeated-failed-login-then-success"
B2 = "password-spray-across-accounts"
B3 = "concurrent-sessions-different-hosts"
B4 = "service-account-interactive-auth"
B5 = "mfa-bypass-on-required-account"
BEHAVIOUR_IDS = (B1, B2, B3, B4, B5)


@dataclass(frozen=True)
class DetectionParams:
    b1_window_s: int
    b1_fail_threshold: int
    b2_window_s: int
    b2_distinct_threshold: int
    b3_window_s: int
    b3_host_threshold: int

    @classmethod
    def from_compiled_specs(cls, directory: Path = COMPILED_DIR) -> "DetectionParams":
        specs = {}
        for behaviour_id in BEHAVIOUR_IDS:
            path = directory / f"{behaviour_id}.compiled.json"
            specs[behaviour_id] = json.loads(path.read_text(encoding="utf-8"))

        b1, b2, b3 = specs[B1], specs[B2], specs[B3]
        _require(b1["recipe"] == "SequenceThenTrigger"
                 and b1["countEventType"] == "login_failure"
                 and b1["triggerEventType"] == "login_success"
                 and b1["groupingKey"] == "account_id", B1, "failures-then-success by account_id")
        _require(b2["recipe"] == "DistinctCountWithinWindow"
                 and b2["filterEventType"] == "login_failure"
                 and b2["groupingKey"] == "source_host"
                 and b2["distinctField"] == "account_id", B2, "distinct account_id over failures by source_host")
        _require(b3["recipe"] == "DistinctCountWithinWindow"
                 and b3["filterEventType"] == "login_success"
                 and b3["groupingKey"] == "account_id"
                 and b3["distinctField"] == "source_host", B3, "distinct source_host over successes by account_id")
        _require(specs[B4]["recipe"] == "PolicyCompare"
                 and specs[B4]["comparisonOp"] == "notEqual"
                 and specs[B4]["filterEventType"] == "login_success", B4, "auth_method != expected on success")
        _require(specs[B5]["recipe"] == "PolicyCompare"
                 and specs[B5]["comparisonOp"] == "falseWhenRequired"
                 and specs[B5]["filterEventType"] == "login_success", B5, "mfa_used false when required on success")

        params = cls(
            b1_window_s=int(b1["timeWindowSeconds"]),
            b1_fail_threshold=int(b1["countThreshold"]),
            b2_window_s=int(b2["timeWindowSeconds"]),
            b2_distinct_threshold=int(b2["distinctThreshold"]),
            b3_window_s=int(b3["timeWindowSeconds"]),
            b3_host_threshold=int(b3["distinctThreshold"]),
        )
        # The incident builders construct pairs / small groups around these
        # values; fail loudly if a spec change invalidates that construction.
        _require(params.b1_fail_threshold >= 3, B1, "threshold >= 3 (builders need room below it)")
        _require(params.b2_distinct_threshold >= 3, B2, "distinct threshold >= 3")
        _require(params.b3_host_threshold == 2, B3, "distinct-host threshold == 2 (pair construction)")
        return params


def _require(ok: bool, behaviour_id: str, expected: str) -> None:
    if not ok:
        raise ValueError(
            f"Compiled spec for {behaviour_id} no longer matches the generator's "
            f"assumption ({expected}); update scripts/datagen before generating data."
        )
