"""End-to-end smoke test of a RUNNING container over HTTP: analyse a report, compile, execute, fetch an evidence record.

    docker run -d -p 127.0.0.1:8124:8000 -e SF_API_TOKENS=ci:secret:analyst sentinel-forge:full
    python scripts/smoke_container.py --url http://127.0.0.1:8124 --token secret --expect-engine spark

Exit code 0 only if every step behaves. Uses nothing but the standard library so it can run anywhere.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

REPORT = ("Password spraying brief. Alert when 4 or more distinct accounts fail from the same source host within 10 minutes. "
          "Fields required: `account_id`, `event_type`, `timestamp` and `source_host`.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", required=True)
    ap.add_argument("--expect-engine", choices=["spark", "reference"])
    a = ap.parse_args()
    H = {"Authorization": f"Bearer {a.token}", "Content-Type": "application/json"}

    def call(method, path, body=None):
        req = urllib.request.Request(a.url + path, data=None if body is None else json.dumps(body).encode(), headers=H, method=method)
        try:
            return json.load(urllib.request.urlopen(req, timeout=180))
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"FAIL {method} {path}: HTTP {exc.code} {exc.read()[:300]!r}")

    def wait(job):
        end = time.time() + 300
        while time.time() < end:
            j = call("GET", f"/api/jobs/{job}")
            if j["state"] in ("ready", "needs_review", "rejected", "completed", "failed"):
                return j
            time.sleep(0.5)
        raise SystemExit("FAIL job timed out")

    cfg = call("GET", "/api/config")
    print("engine selected:", cfg["engine"]["selected"])
    if a.expect_engine and cfg["engine"]["selected"] != a.expect_engine:
        raise SystemExit(f"FAIL expected engine {a.expect_engine}: {cfg['engine']}")
    rep = call("POST", "/api/reports", {"text": REPORT})
    an = call("POST", f"/api/reports/{rep['id']}/analyses", {"extractor": "evidence"})
    j = wait(an["jobId"])
    assert j["state"] == "ready", j
    rv = call("POST", f"/api/analyses/{an['analysisId']}/rule")
    assert rv["compiled"]["distinctThreshold"] == 4 and rv["compiled"]["timeWindowSeconds"] == 600, rv["compiled"]
    run = call("POST", f"/api/rule-versions/{rv['id']}/runs", {})
    t0 = time.time()
    j = wait(run["jobId"])
    assert j["state"] == "completed", j.get("error")
    info = call("GET", f"/api/runs/{run['runId']}")
    alerts = call("GET", f"/api/runs/{run['runId']}/alerts")
    assert alerts["total"] >= 1, alerts
    ev = call("GET", f"/api/runs/{run['runId']}/alerts/{alerts['alerts'][0]['triggeringEventId']}/evidence")
    assert ev["fired"]["supportingEvents"] and all(p["quoteVerified"] for p in ev["why"]["passages"])
    print(f"OK  engine={info['summary']['engine']} spark={info['summary'].get('sparkVersion')} run={time.time() - t0:.1f}s "
          f"alerts={alerts['total']} quarantined={info['summary']['counts']['quarantined']} duplicates={info['summary']['counts']['duplicatesDropped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
