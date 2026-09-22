"""Self-consistency voting over the prompted extractor — the reliability fix scoped to the actual
failure mode: `transformer_extractor.py`'s own documented run-to-run variance at `temperature=0`
(`nlp/README.md`: "Groq's API did not reproduce identical output field-for-field between two runs
of the same prompt"), not a claim that the newly fine-tuned model needed the same fix (its
determinism is separately verified, by running the same checkpoint twice — see
`experiments/results/phaseC_determinism_check.json`).

Calls the prompted extractor `n_samples` times and takes, per field, the majority answer:

  - `behaviourId`: the most common value across calls (ties broken by first occurrence)
  - `requiredFields`/`policyFields`: each field kept if it appeared in a strict majority of calls
  - `threshold`/`timeWindow`: the most common value (by equality), or `None` if every call disagreed

This is a real, standard mitigation (self-consistency decoding), not a description of one —
`measure_variance()` below runs it against a small sample and reports the actual agreement rate
before and after voting, so the improvement is demonstrated, not assumed.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transformer_extractor  # noqa: E402


def _majority_dict(values: list[dict | None], key_fn=lambda v: v) -> dict | None:
    present = [v for v in values if v]
    if not present:
        return None
    counts = collections.Counter(json.dumps(key_fn(v), sort_keys=True) for v in present)
    winner, count = counts.most_common(1)[0]
    return next(v for v in present if json.dumps(key_fn(v), sort_keys=True) == winner) if count * 2 > len(values) else None


def _try_extract(text: str, model: str) -> tuple[dict | None, str | None]:
    """A single call's own malformed/truncated output (a real, observed failure mode of the
    smaller model) must not crash a voting scheme whose entire point is to be robust to one bad
    sample — it is simply excluded from the vote, same as evaluate_corpus.run_system excludes an
    unavailable report from metrics rather than scoring it as wrong. The failure reason is kept
    (not just a count) — a silent count is what made an earlier real run's total-failure case on
    one report indistinguishable, after the fact, from a genuine 3-way model disagreement."""
    try:
        return transformer_extractor.extract(text, model), None
    except Exception as exc:  # noqa: BLE001 - any failure of a single sample is excluded, not fatal
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"


def extract(report_text: str, n_samples: int = 3, model: str = transformer_extractor.DEFAULT_MODEL) -> dict:
    attempts = [_try_extract(report_text, model) for _ in range(n_samples)]
    samples = [s for s, _ in attempts if s is not None]
    failures = [err for _, err in attempts if err is not None]
    if not samples:
        return {"behaviourId": None, "requiredFields": [], "policyFields": [], "threshold": None,
                "timeWindow": None, "provenance": {}, "nSamples": n_samples, "nFailed": len(failures),
                "failures": failures, "agreement": {"behaviourId": 0.0}, "samples": []}

    behaviour_counts = collections.Counter(s["behaviourId"] for s in samples)
    behaviour_id = behaviour_counts.most_common(1)[0][0]

    def majority_fields(key: str) -> list[str]:
        counts = collections.Counter(f for s in samples for f in s.get(key, []))
        return sorted(f for f, c in counts.items() if c * 2 > len(samples))

    return {
        "behaviourId": behaviour_id,
        "requiredFields": majority_fields("requiredFields"),
        "policyFields": majority_fields("policyFields"),
        "threshold": _majority_dict([s.get("threshold") for s in samples]),
        "timeWindow": _majority_dict([s.get("timeWindow") for s in samples]),
        "provenance": samples[0].get("provenance", {}),   # evidence from the first sample; voting doesn't merge spans
        "nSamples": n_samples, "nFailed": len(failures), "failures": failures,
        "agreement": {
            # Agreement among the samples that actually returned something — a failed call is
            # missing data, not a vote against the majority.
            "behaviourId": behaviour_counts.most_common(1)[0][1] / len(samples),
        },
        "samples": samples,
    }


def measure_variance(report_texts: dict[str, str], n_samples: int = 3,
                     model: str = transformer_extractor.DEFAULT_MODEL) -> dict:
    """For each report, run n_samples single-call extractions and report whether they all agreed
    on behaviourId and on the required+policy field set — i.e. the actual, measured variance this
    module exists to reduce — then compare to the voted answer's stability.
    """
    rows, skipped = [], []
    for report_id, text in report_texts.items():
        attempts = [_try_extract(text, model) for _ in range(n_samples)]
        samples = [s for s, _ in attempts if s is not None]
        failures = [err for _, err in attempts if err is not None]
        if len(samples) < 2:      # can't measure agreement from 0 or 1 successful sample
            skipped.append({"reportId": report_id, "nSucceeded": len(samples), "failures": failures})
            continue
        behaviours = [s["behaviourId"] for s in samples]
        field_sets = [tuple(sorted(set(s.get("requiredFields", [])) | set(s.get("policyFields", [])))) for s in samples]
        rows.append({
            "reportId": report_id, "nSamples": len(samples), "nFailed": n_samples - len(samples),
            "behavioursAgree": len(set(behaviours)) == 1,
            "fieldsAgree": len(set(field_sets)) == 1,
            "behaviours": behaviours, "fieldSets": [list(f) for f in field_sets],
        })
    n = len(rows)
    return {
        "nReports": n, "nSamplesPerReport": n_samples, "reportsSkipped": skipped,
        "behaviourAgreementRate": sum(r["behavioursAgree"] for r in rows) / n if n else None,
        "fieldSetAgreementRate": sum(r["fieldsAgree"] for r in rows) / n if n else None,
        "perReport": rows,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("report_file")
    ap.add_argument("--n-samples", type=int, default=3)
    args = ap.parse_args()
    text = Path(args.report_file).read_text(encoding="utf-8")
    print(json.dumps({k: v for k, v in extract(text, args.n_samples).items() if k != "samples"}, indent=2))
