"""Test the test: can the differential harness actually see the defect classes it claims to guard against?

The reference engine's source is mutated in memory (never on disk) to reintroduce one classic defect at a
time. The Spark executor runs ONCE per configuration; every mutant is then compared against that same
Spark output. A mutant that produces no disagreement is a hole in the test, not a good sign.

    python scripts/verify/mutation_check.py --cases 60 --out experiments/results/differential_mutation_check.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from differential import _SC, compare, spec_for  # noqa: E402
from scenarios import gen_scenarios  # noqa: E402
from sentinelforge import behaviours as B  # noqa: E402
from sentinelforge import spark_engine  # noqa: E402
from sentinelforge.compile import compile_spec  # noqa: E402

SRC = (REPO / "sentinelforge" / "refengine.py").read_text(encoding="utf-8")

# name -> (behaviours it applies to, [(old, new), ...], defect it models)
MUTANTS = {
    "open-window-lower-bound": (["repeated-failed-login-then-success", "password-spray-across-accounts", "multi-host-authentication"],
                                [('trig["_micros"] - w <= f["_micros"] <= trig["_micros"]', 'trig["_micros"] - w < f["_micros"] <= trig["_micros"]'),
                                 ('r["_micros"] - w <= x["_micros"] <= r["_micros"]', 'r["_micros"] - w < x["_micros"] <= r["_micros"]')],
                                "boundary event exactly W earlier excluded"),
    "truncate-to-seconds": (["repeated-failed-login-then-success", "password-spray-across-accounts", "multi-host-authentication"],
                            [("    return micros + int((frac or \"\").ljust(6, \"0\")) if frac else micros",
                              "    return micros")],
                            "sub-second precision discarded before window arithmetic"),
    "no-deduplication": (["repeated-failed-login-then-success", "password-spray-across-accounts", "multi-host-authentication"],
                         [("    clean = [min(rows, key=lambda r: (r[\"_micros\"], _row_content(r))) for rows in by_id.values()]",
                           "    clean = ok")],
                         "redelivered events counted twice"),
    "events-not-distinct": (["password-spray-across-accounts", "multi-host-authentication"],
                            [("distinct = {x[dc] for x in inside}", "distinct = [x[dc] for x in inside]")],
                            "count of events instead of distinct values"),
    "no-rising-edge": (["password-spray-across-accounts", "multi-host-authentication"],
                       [("if breaching and not previously:", "if breaching:")],
                       "alert on every breaching event, not once per incident"),
    "null-log-value-as-compliant": (["auth-method-policy-violation", "mfa-missing-on-required-account"],
                                    [('status, reason = "insufficient_context", "log_value_null"', 'status, reason = "no_alert", "compliant"')],
                                    "an unobserved value treated as compliant"),
    "policy-conflict-first-wins": (["auth-method-policy-violation", "mfa-missing-on-required-account"],
                                   [('if len(non_null) + (1 if has_null else 0) > 1:', 'if False:')],
                                   "conflicting policy rows silently resolved"),
}


def load_mutant(replacements) -> types.ModuleType:
    code = SRC
    for old, new in replacements:
        if old not in code:
            raise RuntimeError(f"mutation site not found: {old[:60]}")
        code = code.replace(old, new)
    mod = types.ModuleType("refengine_mutant")
    sys.modules["refengine_mutant"] = mod          # dataclasses resolve annotations through sys.modules
    exec(compile(code, "refengine_mutant", "exec"), mod.__dict__)
    return mod


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=60)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    mutants = {k: (v, load_mutant(v[1])) for k, v in MUTANTS.items()}
    killed = defaultdict(int)
    detail: dict = {k: {"behaviours": v[0], "models": v[2], "killedIn": []} for k, v in MUTANTS.items()}
    work = Path(tempfile.mkdtemp(prefix="sf-mut-"))
    for bid in B.BEHAVIOUR_IDS:
        beh = B.BEHAVIOURS[bid]
        windowed = beh.recipe != B.POLICY_COMPARE
        for (w, t), scs in gen_scenarios(bid, args.cases, args.seed).items():
            compiled = compile_spec(spec_for(beh, w, t))
            d = work / f"{bid}-{w}-{t}"
            d.mkdir(parents=True)
            (d / "events.jsonl").write_text("\n".join(json.dumps(e) for s in scs for e in s.events) + "\n", encoding="utf-8")
            (d / "spec.json").write_text(json.dumps(compiled), encoding="utf-8")
            pol = None
            if not windowed:
                (d / "policy.json").write_text(json.dumps({"records": [p for s in scs for p in s.policy]}), encoding="utf-8")
                pol = d / "policy.json"
            run = spark_engine.run_rule(d / "spec.json", d / "events.jsonl", d / "out", pol)
            assert run["status"] == "completed", run
            by_sc: dict = defaultdict(list)
            for a in spark_engine.read_alerts(d / "out"):
                by_sc[_SC.match(a["groupKey"]).group(1)].append(a)
            for name, ((applies, _, _), mod) in mutants.items():
                if bid not in applies:
                    continue
                diffs = sum(1 for s in scs if compare(mod.run_reference(compiled, s.events, s.policy).alerts, by_sc.get(s.id, []), windowed))
                if diffs:
                    killed[name] += 1
                    detail[name]["killedIn"].append({"behaviour": bid, "window": w, "threshold": t, "scenariosDisagreeing": diffs})
    survivors = [k for k in MUTANTS if killed[k] == 0]
    for k, v in detail.items():
        print(f"{'KILLED  ' if killed[k] else 'SURVIVED'} {k:32s} {v['models']}")
    print(f"\n{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} mutants detected")
    if args.out:
        args.out.write_text(json.dumps({"seed": args.seed, "casesPerBehaviour": args.cases, "mutants": detail,
                                        "survivors": survivors}, indent=2), encoding="utf-8")
    return 0 if not survivors else 1


if __name__ == "__main__":
    sys.exit(main())
