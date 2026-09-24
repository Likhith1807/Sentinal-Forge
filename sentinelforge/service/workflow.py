"""The single workflow the product exists for:

    paste report -> inspect extracted conditions + evidence -> validate against available data ->
    compile -> execute a fresh job -> inspect matching events -> approve or refine

Design rules enforced here (each has a test in tests/service):

* Every result belongs to a specific report, rule VERSION, dataset VERSION and run. Nothing is "latest".
* The compiled rule is always built server-side from a stored, evidence-backed analysis. Approving takes a
  rule-version id and a run id - never a specification from the client.
* A rule whose data dependencies are not met is never run and never adapted; it is paused with the exact
  conditions that cannot be evaluated, and resuming needs a successful revalidation run on current data.
* Runs are isolated: UUID directories, atomic files, failures preserved.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .. import behaviours as B
from ..compile import RuleBuildError, compile_spec
from ..pipeline import Analysis, analyze
from ..validation import check_dataset
from . import datasets as ds
from . import engines, evidence, extractors, schema_impact
from .jobs import JobHandle, JobRunner
from .settings import REPO_ROOT, Settings
from .store import Conflict, Store, dumps, loads, new_id, now, row_dict

DEMO_DATASET_ID = "demo-auth"
_UUIDISH = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class NotFound(KeyError):
    pass


class InvalidRequest(ValueError):
    def __init__(self, message: str, issues: list | None = None):
        super().__init__(message)
        self.issues = issues or []


class Blocked(Exception):
    """The data cannot evaluate this rule. `.dependencies` lists exactly what is missing / mistyped."""
    def __init__(self, message: str, dependencies: list):
        super().__init__(message)
        self.dependencies = dependencies


class StateConflict(Exception):
    pass


def _clean_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _UUIDISH.match(value):
        raise InvalidRequest(f"invalid {what}")
    return value


class Workspace:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.settings.ensure_dirs()
        self.store = Store(self.settings.db_path)
        self.jobs = JobRunner(self.store, self.settings.job_workers)
        self.jobs.recover()
        self._seed_demo()

    # ------------------------------------------------------------------------------- seeding
    def _seed_demo(self) -> None:
        if self.store.one("SELECT id FROM datasets WHERE id = ?", (DEMO_DATASET_ID,)) is None:
            demo = REPO_ROOT / "data" / "demo"
            if (demo / "auth_events.jsonl").exists():
                ds.register(self.store, self.settings, "Demonstration authentication log", "demonstration",
                            demo / "auth_events.jsonl", demo / "account_policy.json",
                            "725 synthetic events over two days with 34 planted, graded incidents and realistic data-quality problems.",
                            dataset_id=DEMO_DATASET_ID)

    def shutdown(self) -> None:
        self.jobs.shutdown()

    # ------------------------------------------------------------------------------- reports
    def create_report(self, text: str, title: str | None = None, source: str = "pasted", user: str = "anonymous") -> dict:
        if not isinstance(text, str):
            raise InvalidRequest("report text must be a string")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if "\x00" in text:
            raise InvalidRequest("report text contains NUL bytes")
        if len(text.encode("utf-8")) > self.settings.max_report_bytes:
            raise InvalidRequest(f"report is larger than the {self.settings.max_report_bytes:,}-byte limit")
        if len(text.strip()) < 20:
            raise InvalidRequest("report text is too short to contain a detection condition")
        if not title:
            first = next((ln for ln in text.splitlines() if ln.strip()), "Untitled report")
            title = re.sub(r"^[#*\s>-]+|[*\s]+$", "", first)[:90] or "Untitled report"
        rid = new_id()
        with self.store.tx() as c:
            c.execute("INSERT INTO reports VALUES (?,?,?,?,?,?,?)",
                      (rid, title[:200], text, hashlib.sha256(text.encode("utf-8")).hexdigest(), source, user, now()))
        return self.get_report(rid)

    def get_report(self, report_id: str) -> dict:
        r = self.store.one("SELECT * FROM reports WHERE id = ?", (_clean_id(report_id, "report id"),))
        if r is None:
            raise NotFound(f"report {report_id}")
        return dict(r)

    def list_reports(self, limit: int = 50) -> list[dict]:
        rows = self.store.q("SELECT id, title, source, created_at, created_by, text_sha256, length(text) AS chars FROM reports ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            a = self.store.one("SELECT id, status, extractor FROM analyses WHERE report_id = ? ORDER BY created_at DESC LIMIT 1", (r["id"],))
            out.append({**dict(r), "latestAnalysis": dict(a) if a else None})
        return out

    # ------------------------------------------------------------------------------ analysis
    def submit_analysis(self, report_id: str, extractor: str | None = None, dataset_id: str | None = None, user: str = "anonymous") -> dict:
        report = self.get_report(report_id)
        available = {e.id: e for e in extractors.list_extractors()}
        extractor = extractor or extractors.default_extractor()
        if extractor not in available:
            raise InvalidRequest(f"unknown extractor {extractor!r}")
        if not available[extractor].available:
            raise InvalidRequest(f"extractor {extractor!r} is unavailable: {available[extractor].reason}")
        dataset_id = dataset_id or (DEMO_DATASET_ID if self.store.one("SELECT id FROM datasets WHERE id=?", (DEMO_DATASET_ID,)) else None)
        version = None
        if dataset_id:
            version = ds.get(self.store, _clean_id(dataset_id, "dataset id"))["currentVersion"] if self._dataset_exists(dataset_id) else None
            if version is None:
                raise NotFound(f"dataset {dataset_id}")
        aid = new_id()
        with self.store.tx() as c:
            c.execute("INSERT INTO analyses(id, report_id, extractor, dataset_id, dataset_version, status, created_at) VALUES (?,?,?,?,?,?,?)",
                      (aid, report["id"], extractor, dataset_id, version, "queued", now()))
        job_id = self.jobs.submit("analyze", aid, lambda h: self._do_analysis(h, aid), {"reportId": report["id"], "extractor": extractor})
        return {"analysisId": aid, "jobId": job_id}

    def _dataset_exists(self, dataset_id: str) -> bool:
        return self.store.one("SELECT id FROM datasets WHERE id=?", (dataset_id,)) is not None

    def _do_analysis(self, job: JobHandle, analysis_id: str) -> None:
        a = self.store.one("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
        report = self.get_report(a["report_id"])
        result: dict = {}
        status = "failed"
        try:
            job.set("extracting")
            self._set_analysis(analysis_id, "extracting")
            guard = self._guard(report["text"])
            try:
                proposal, meta = extractors.extract(a["extractor"], report["text"])
            except extractors.ExtractorRefused as exc:
                result = {"status": "rejected", "behaviourId": None, "error": None, "extractorMeta": {"extractor": a["extractor"]},
                          "reconciliation": {"status": "rejected", "reasons": [{"code": "PROMPT_INJECTION_SUSPECTED", "severity": "reject",
                                                                               "message": str(exc), "evidence": []}], "conditions": {}, "uncertainties": []},
                          "guard": guard}
                status = "rejected"
                job.set("rejected", "prompt-injection guard")
                return self._save_analysis(analysis_id, status, result)
            except extractors.ExtractorUnavailable as exc:
                raise RuntimeError(str(exc)) from exc
            job.set("validating")
            self._set_analysis(analysis_id, "validating")
            profile, dataset_info = None, None
            if a["dataset_id"]:
                d = ds.get(self.store, a["dataset_id"], a["dataset_version"])
                profile = d["profile"]
                dataset_info = {"id": d["id"], "name": d["name"], "kind": d["kind"], "version": d["version"], "fingerprint": d["fingerprint"]}
            res: Analysis = analyze(report["text"], proposal, profile=profile)
            result = {**res.to_dict(), "extractorMeta": meta, "dataset": dataset_info, "guard": guard, "proposal": proposal}
            status = {"compiled": "ready", "needs_review": "needs_review", "rejected": "rejected", "data_unsupported": "rejected"}[res.status]
            if res.status == "data_unsupported":
                result["rejectionKind"] = "data"
            result["status"] = status
            self._save_analysis(analysis_id, status, result)
            job.set(status)
        except Exception as exc:  # noqa: BLE001
            self._save_analysis(analysis_id, "failed", {"status": "failed", "error": {"kind": type(exc).__name__, "message": str(exc)}})
            job.set("failed", error={"kind": type(exc).__name__, "message": str(exc)})

    def _guard(self, text: str) -> dict:
        try:
            extractors._nlp_on_path()
            import injection_guard
            g = injection_guard.scan(text)
            return {"flagged": g.flagged, "matches": g.matches}
        except Exception:  # noqa: BLE001 - the guard is advisory for non-LLM extractors
            return {"flagged": False, "matches": []}

    def _set_analysis(self, analysis_id: str, status: str) -> None:
        with self.store.tx() as c:
            c.execute("UPDATE analyses SET status = ? WHERE id = ?", (status, analysis_id))

    def _save_analysis(self, analysis_id: str, status: str, result: dict) -> None:
        meta = result.get("extractorMeta")
        with self.store.tx() as c:
            c.execute("UPDATE analyses SET status=?, result=?, extractor_meta=? WHERE id=?", (status, dumps(result), dumps(meta), analysis_id))

    def get_analysis(self, analysis_id: str) -> dict:
        a = self.store.one("SELECT * FROM analyses WHERE id = ?", (_clean_id(analysis_id, "analysis id"),))
        if a is None:
            raise NotFound(f"analysis {analysis_id}")
        d = row_dict(a, ("result", "extractor_meta"))
        job = self.store.one("SELECT id FROM jobs WHERE ref = ? AND kind='analyze' ORDER BY created_at DESC LIMIT 1", (analysis_id,))
        d["jobId"] = job["id"] if job else None
        rv = self.store.q("SELECT id, version, state FROM rule_versions WHERE analysis_id = ? ORDER BY created_at", (analysis_id,))
        d["ruleVersions"] = [dict(r) for r in rv]
        return d

    # ------------------------------------------------------------------------------- rules
    def create_rule_version(self, analysis_id: str, user: str = "anonymous", name: str | None = None) -> dict:
        a = self.get_analysis(analysis_id)
        res = a.get("result") or {}
        if a["status"] != "ready" or not res.get("compiled"):
            raise StateConflict(f"analysis is {a['status']!r}; only a 'ready' analysis (every condition evidenced, data supported) can become a rule")
        compiled = res["compiled"]
        report = self.get_report(a["report_id"])
        with self.store.tx() as c:
            rule = c.execute("SELECT id FROM rules WHERE report_id = ? AND behaviour_id = ?", (report["id"], compiled["behaviourId"])).fetchone()
            if rule is None:
                rid = new_id()
                beh = B.get(compiled["behaviourId"])
                c.execute("INSERT INTO rules VALUES (?,?,?,?,?)", (rid, report["id"], beh.id, name or f"{beh.display_name} - {report['title'][:60]}", now()))
            else:
                rid = rule["id"]
            version = (c.execute("SELECT COALESCE(MAX(version),0) AS v FROM rule_versions WHERE rule_id = ?", (rid,)).fetchone()["v"]) + 1
            rvid = new_id()
            c.execute("UPDATE rule_versions SET state='superseded', previous_state=state WHERE rule_id = ? AND state = 'draft'", (rid,))
            c.execute("INSERT INTO rule_versions(id, rule_id, version, analysis_id, compiled, rule_hash, compiler_version, origin, overrides, state, created_by, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (rvid, rid, version, analysis_id, dumps(compiled), compiled["ruleHash"], compiled["compilerVersion"], "evidence", None, "draft", user, now()))
        return self.get_rule_version(rvid)

    def get_rule_version(self, rule_version_id: str) -> dict:
        r = self.store.one("SELECT rv.*, r.report_id, r.behaviour_id, r.name FROM rule_versions rv JOIN rules r ON r.id = rv.rule_id WHERE rv.id = ?",
                           (_clean_id(rule_version_id, "rule version id"),))
        if r is None:
            raise NotFound(f"rule version {rule_version_id}")
        d = row_dict(r, ("compiled", "overrides", "pause_reason"))
        d["behaviour"] = {"id": d["behaviour_id"], "name": B.get(d["behaviour_id"]).display_name,
                          "checks": B.get(d["behaviour_id"]).checks, "limitations": list(B.get(d["behaviour_id"]).limitations)}
        d["decisions"] = [dict(x) for x in self.store.q("SELECT * FROM decisions WHERE rule_version_id = ? ORDER BY created_at", (rule_version_id,))]
        d["runs"] = [self._run_brief(x) for x in self.store.q("SELECT * FROM runs WHERE rule_version_id = ? ORDER BY created_at DESC", (rule_version_id,))]
        d["siblings"] = [dict(x) for x in self.store.q("SELECT id, version, state, origin FROM rule_versions WHERE rule_id = ? ORDER BY version", (d["rule_id"],))]
        return d

    def list_rule_versions(self, states: tuple | None = None) -> list[dict]:
        sql = "SELECT rv.id FROM rule_versions rv ORDER BY rv.created_at DESC"
        out = []
        for r in self.store.q(sql):
            rv = self.get_rule_version(r["id"])
            if states is None or rv["state"] in states:
                out.append(rv)
        return out

    def refine_rule_version(self, rule_version_id: str, overrides: dict, note: str, user: str = "anonymous") -> dict:
        """The analyst changes a number. The rule is rebuilt SERVER-SIDE from the evidence-backed analysis, strictly
        re-validated, and stored as a new version that records exactly what differs from the report's own evidence."""
        rv = self.get_rule_version(rule_version_id)
        if rv["state"] not in ("draft", "approved", "paused"):
            raise StateConflict(f"rule version is {rv['state']!r} and cannot be refined")
        if not note or len(note.strip()) < 5:
            raise InvalidRequest("a refinement needs a note saying why")
        allowed = {"count", "windowSeconds"}
        if not isinstance(overrides, dict) or not overrides or set(overrides) - allowed:
            raise InvalidRequest(f"overrides must be a non-empty object with keys from {sorted(allowed)}")
        analysis = self.get_analysis(rv["analysis_id"])
        spec = json.loads(json.dumps(analysis["result"]["reconciliation"]["spec"]))
        base_compiled = rv["compiled"]
        changed, before, after = [], {}, {}
        beh = B.get(rv["behaviour_id"])
        if beh.recipe == B.POLICY_COMPARE:
            raise InvalidRequest(f"{beh.display_name} has no numbers to refine")
        cond = spec.setdefault("conditions", {})
        if "count" in overrides:
            cur = (base_compiled.get("countThreshold") or base_compiled.get("distinctThreshold"))
            key = next(iter(beh.threshold_aliases))
            cond.setdefault("count", {"semantics": beh.count_semantics, "comparator": "gte"})["value"] = overrides["count"]
            spec["threshold"] = {key: overrides["count"]}
            changed.append("count")
            before["count"], after["count"] = cur, overrides["count"]
        if "windowSeconds" in overrides:
            cond.setdefault("window", {})["amount"] = overrides["windowSeconds"]
            cond["window"]["unit"] = "seconds"
            spec["timeWindow"] = {"amount": overrides["windowSeconds"], "unit": "seconds"}
            changed.append("windowSeconds")
            before["windowSeconds"], after["windowSeconds"] = base_compiled.get("timeWindowSeconds"), overrides["windowSeconds"]
        try:
            compiled = compile_spec(spec)
        except RuleBuildError as exc:
            raise InvalidRequest(str(exc), [i.to_dict() for i in exc.issues]) from exc
        ov = {"changed": changed, "from": before, "to": after, "note": note.strip(), "by": user, "at": now()}
        with self.store.tx() as c:
            version = (c.execute("SELECT MAX(version) AS v FROM rule_versions WHERE rule_id = ?", (rv["rule_id"],)).fetchone()["v"]) + 1
            new_id_ = new_id()
            c.execute("UPDATE rule_versions SET state='superseded', previous_state=state WHERE id = ? AND state IN ('draft','approved','paused')", (rule_version_id,))
            c.execute("INSERT INTO rule_versions(id, rule_id, version, analysis_id, compiled, rule_hash, compiler_version, origin, overrides, state, created_by, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (new_id_, rv["rule_id"], version, rv["analysis_id"], dumps(compiled), compiled["ruleHash"], compiled["compilerVersion"],
                       "analyst-refined", dumps(ov), "draft", user, now()))
            c.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (new_id(), rule_version_id, None, "refine", user, note.strip(), now()))
        return self.get_rule_version(new_id_)

    def decide(self, rule_version_id: str, decision: str, analyst: str, note: str = "", run_id: str | None = None) -> dict:
        """approve: needs a COMPLETED run of exactly this rule version. reject: retire it. Idempotence and races are
        settled by a compare-and-set on the rule version's state."""
        if decision not in ("approve", "reject"):
            raise InvalidRequest("decision must be 'approve' or 'reject' (use refine to change a value)")
        rv = self.get_rule_version(rule_version_id)
        if decision == "approve":
            if not run_id:
                raise InvalidRequest("approving needs the run whose results you reviewed (run_id)")
            run = self.store.one("SELECT * FROM runs WHERE id = ?", (_clean_id(run_id, "run id"),))
            if run is None or run["rule_version_id"] != rv["id"]:
                raise InvalidRequest("that run does not belong to this rule version")
            if run["state"] != "completed" or run["purpose"] != "execute":
                raise StateConflict("only a completed execution run can be reviewed for approval")
        try:
            with self.store.tx() as c:
                self.store.cas(c, "rule_versions", rv["id"], "approved" if decision == "approve" else "retired",
                               ("draft",) if decision == "approve" else ("draft", "approved", "paused"))
                c.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (new_id(), rv["id"], run_id, decision, analyst, note, now()))
        except Conflict as exc:
            raise StateConflict(f"rule version is {rv['state']!r}: {exc}") from exc
        return self.get_rule_version(rv["id"])

    # ---------------------------------------------------------------------------------- runs
    def preflight(self, rv: dict, dataset: dict) -> None:
        check = check_dataset(rv["behaviour_id"], dataset["profile"])
        if not check.supported:
            raise Blocked("the dataset cannot evaluate this rule", [d.to_dict() for d in check.blocking])

    def submit_run(self, rule_version_id: str, dataset_id: str | None = None, user: str = "anonymous", purpose: str = "execute") -> dict:
        rv = self.get_rule_version(rule_version_id)
        if purpose == "execute" and rv["state"] not in ("draft", "approved"):
            raise StateConflict(f"rule version is {rv['state']!r}; "
                                + ("it is paused until it is revalidated (resume)" if rv["state"] == "paused" else "only draft or approved rules can run"))
        if purpose == "revalidate" and rv["state"] != "paused":
            raise StateConflict("only a paused rule is revalidated")
        dataset_id = dataset_id or DEMO_DATASET_ID
        if not self._dataset_exists(_clean_id(dataset_id, "dataset id")):
            raise NotFound(f"dataset {dataset_id}")
        d = ds.get(self.store, dataset_id)
        self.preflight(rv, d)                                       # never run what the data cannot evaluate
        engine = engines.choose(self.settings.engine, Path(d["eventsPath"]), self.settings.reference_max_bytes)
        run_id = new_id()
        with self.store.tx() as c:
            c.execute("INSERT INTO runs(id, rule_version_id, dataset_id, dataset_version, dataset_fingerprint, purpose, engine, state, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (run_id, rv["id"], d["id"], d["version"], d["fingerprint"], purpose, engine, "queued", user, now()))
        job_id = self.jobs.submit("run", run_id, lambda h: self._do_run(h, run_id), {"ruleVersionId": rv["id"], "datasetId": d["id"], "purpose": purpose})
        return {"runId": run_id, "jobId": job_id, "engine": engine}

    def _do_run(self, job: JobHandle, run_id: str) -> None:
        r = self.store.one("SELECT * FROM runs WHERE id = ?", (run_id,))
        rv = self.get_rule_version(r["rule_version_id"])
        d = ds.get(self.store, r["dataset_id"], r["dataset_version"])
        run_dir = self.settings.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        job.set("running")
        with self.store.tx() as c:
            c.execute("UPDATE runs SET state='running', started_at=?, run_dir=? WHERE id=?", (now(), str(run_dir), run_id))
        spec_path = run_dir / "rule.compiled.json"
        spec_path.write_text(json.dumps(rv["compiled"], indent=2), encoding="utf-8")
        result = engines.run_rule(r["engine"], spec_path, Path(d["eventsPath"]), run_dir / "out",
                                  Path(d["policyPath"]) if d["policyPath"] else None, self.settings.spark_heap, self.settings.run_timeout_s)
        ok = result.get("status") == "completed"
        summary = {k: result.get(k) for k in ("counts", "timings", "sparkVersion", "javaVersion", "engine", "elapsedSeconds", "ruleHash", "compilerVersion", "options")}
        with self.store.tx() as c:
            c.execute("UPDATE runs SET state=?, finished_at=?, summary=?, error=? WHERE id=?",
                      ("completed" if ok else "failed", now(), dumps(summary), dumps(result.get("error")) if not ok else None, run_id))
            if ok and r["purpose"] == "revalidate":
                cur = c.execute("SELECT state, previous_state FROM rule_versions WHERE id = ?", (rv["id"],)).fetchone()
                if cur["state"] == "paused":
                    c.execute("UPDATE rule_versions SET state = ?, previous_state = NULL, pause_reason = NULL WHERE id = ?",
                              (cur["previous_state"] or "draft", rv["id"]))
                    c.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
                              (new_id(), rv["id"], run_id, "resume", r["created_by"] or "system", "revalidated on current data", now()))
        job.set("completed" if ok else "failed", error=None if ok else result.get("error"))

    def _run_brief(self, r) -> dict:
        d = row_dict(r, ("summary", "error"))
        return {"id": d["id"], "state": d["state"], "purpose": d["purpose"], "engine": d["engine"], "datasetId": d["dataset_id"],
                "datasetVersion": d["dataset_version"], "createdAt": d["created_at"], "finishedAt": d["finished_at"],
                "counts": (d.get("summary") or {}).get("counts"), "error": d.get("error")}

    def get_run(self, run_id: str) -> dict:
        r = self.store.one("SELECT * FROM runs WHERE id = ?", (_clean_id(run_id, "run id"),))
        if r is None:
            raise NotFound(f"run {run_id}")
        d = row_dict(r, ("summary", "error"))
        rv = self.get_rule_version(d["rule_version_id"])
        analysis = self.get_analysis(rv["analysis_id"])
        report = self.get_report(analysis["report_id"])
        dv = ds.get(self.store, d["dataset_id"], d["dataset_version"])
        cur = ds.get(self.store, d["dataset_id"])
        job = self.store.one("SELECT id FROM jobs WHERE ref = ? AND kind='run'", (run_id,))
        d.update(jobId=job["id"] if job else None, ruleVersion=rv, report={"id": report["id"], "title": report["title"]},
                 dataset={k: dv[k] for k in ("id", "name", "kind", "version", "fingerprint", "rowCount")},
                 freshness={"origin": "fresh", "executedBy": "this system", "dataKind": dv["kind"],
                            "stale": cur["version"] != dv["version"] or cur["fingerprint"] != dv["fingerprint"],
                            "label": ("Fresh run on demonstration data" if dv["kind"] == "demonstration" else "Fresh run")
                                     + (" - dataset has changed since" if cur["version"] != dv["version"] else "")})
        return d

    def _alerts_file(self, run: dict) -> Path:
        return Path(run["run_dir"]) / "out" / "alerts.jsonl" if run.get("run_dir") else Path("/nonexistent")

    def alerts(self, run_id: str, status: str | None = None, limit: int = 200, offset: int = 0) -> dict:
        run = self.get_run(run_id)
        path = self._alerts_file(run)
        rows = []
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        rows.append(json.loads(line))
                    if len(rows) >= self.settings.max_alerts_shown:
                        break
        counts: dict = {}
        for a in rows:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        shown = [a for a in rows if status is None or a["status"] == status]
        light = [{k: v for k, v in a.items() if k != "evidence"} | {"evidenceCount": len(a.get("evidence", []))} for a in shown[offset:offset + limit]]
        return {"runId": run_id, "total": len(shown), "statusCounts": counts, "alerts": light,
                "truncated": bool((run.get("summary") or {}).get("counts", {}).get("alertsTruncated"))}

    def quarantine(self, run_id: str) -> list[dict]:
        run = self.get_run(run_id)
        p = Path(run["run_dir"]) / "out" / "quarantine.jsonl" if run.get("run_dir") else None
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p and p.exists() else []

    def evidence_record(self, run_id: str, alert_id: str) -> dict:
        run = self.get_run(run_id)
        alert = None
        path = self._alerts_file(run)
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        a = json.loads(line)
                        if a["triggeringEventId"] == alert_id:
                            alert = a
                            break
        if alert is None:
            raise NotFound(f"alert {alert_id} in run {run_id}")
        rv = run["ruleVersion"]
        analysis = self.get_analysis(rv["analysis_id"])
        report = self.get_report(analysis["report_id"])
        dv = ds.get(self.store, run["dataset_id"], run["dataset_version"])
        insufficient = ((run.get("summary") or {}).get("counts") or {}).get("resultsByStatus", {}).get("insufficient_context", 0)
        return evidence.build(report=report, analysis=analysis, rule_version=rv, run=run, dataset_version=dv, alert=alert,
                              alert_count_context={"insufficient": insufficient})

    # --------------------------------------------------------------------------- schema change
    def apply_schema_change(self, dataset_id: str, drop: list | None, retype: dict | None, user: str = "anonymous") -> dict:
        """Create a new dataset version with columns removed/retyped, then pause exactly the rules it breaks."""
        self.get_dataset(dataset_id)
        before = ds.get(self.store, dataset_id)
        try:
            after = ds.derive_version(self.store, self.settings, dataset_id, drop, retype)
        except ds.DatasetError as exc:
            raise InvalidRequest(str(exc)) from exc
        return self._record_schema_event(before, after, user)

    def restore_dataset(self, dataset_id: str, from_version: int, user: str = "anonymous") -> dict:
        before = ds.get(self.store, dataset_id)
        try:
            after = ds.derive_version(self.store, self.settings, dataset_id, restored_from=from_version)
        except (ds.DatasetError, KeyError) as exc:
            raise InvalidRequest(str(exc)) from exc
        return self._record_schema_event(before, after, user)

    def _record_schema_event(self, before: dict, after: dict, user: str) -> dict:
        impacted, unaffected, resumable = [], [], []
        with self.store.tx() as c:
            rows = c.execute("SELECT rv.id, rv.state, rv.compiled, rv.version, r.behaviour_id, r.name FROM rule_versions rv JOIN rules r ON r.id = rv.rule_id "
                             "WHERE rv.state IN ('draft','approved','paused')").fetchall()
            for r in rows:
                compiled = loads(r["compiled"])
                a = schema_impact.analyse_rule(r["behaviour_id"], compiled, after["profile"])
                item = {"ruleVersionId": r["id"], "ruleName": r["name"], "version": r["version"], "behaviourId": r["behaviour_id"],
                        "stateBefore": r["state"], **a}
                if a["affected"] and r["state"] in ("draft", "approved"):
                    reason = {"datasetId": after["id"], "datasetName": after["name"], "fromVersion": before["version"], "toVersion": after["version"],
                              "blockedConditions": a["blockedConditions"], "at": now()}
                    c.execute("UPDATE rule_versions SET state='paused', previous_state=state, pause_reason=? WHERE id=? AND state IN ('draft','approved')",
                              (dumps(reason), r["id"]))
                    item["stateAfter"] = "paused"
                    impacted.append(item)
                elif a["affected"]:
                    item["stateAfter"] = "paused"
                    impacted.append(item)
                elif r["state"] == "paused":
                    item["stateAfter"] = "paused"
                    item["canResume"] = True
                    resumable.append(item)
                else:
                    item["stateAfter"] = r["state"]
                    unaffected.append(item)
            impact = {"datasetId": after["id"], "fromVersion": before["version"], "toVersion": after["version"], "change": after["change"],
                      "affected": impacted, "unaffected": unaffected, "resumable": resumable,
                      "summary": {"affected": len(impacted), "unaffected": len(unaffected), "resumable": len(resumable)}}
            c.execute("INSERT INTO schema_events VALUES (?,?,?,?,?,?,?,?)",
                      (new_id(), after["id"], before["version"], after["version"], dumps(after["change"]), dumps(impact), user, now()))
        return impact

    def resume_rule_version(self, rule_version_id: str, user: str = "anonymous") -> dict:
        rv = self.get_rule_version(rule_version_id)
        if rv["state"] != "paused":
            raise StateConflict(f"rule version is {rv['state']!r}, not paused")
        dataset_id = (rv.get("pause_reason") or {}).get("datasetId") or DEMO_DATASET_ID
        return self.submit_run(rule_version_id, dataset_id, user, purpose="revalidate")

    # ----------------------------------------------------------------------------- datasets
    def get_dataset(self, dataset_id: str) -> dict:
        if not self._dataset_exists(_clean_id(dataset_id, "dataset id")):
            raise NotFound(f"dataset {dataset_id}")
        d = ds.get(self.store, dataset_id)
        d["versions"] = [{k: v[k] for k in ("version", "change", "fingerprint", "rowCount", "createdAt")} for v in ds.versions(self.store, dataset_id)]
        d["schemaEvents"] = [row_dict(x, ("change", "impact")) for x in self.store.q(
            "SELECT * FROM schema_events WHERE dataset_id = ? ORDER BY created_at DESC LIMIT 20", (dataset_id,))]
        return d

    def register_upload(self, name: str, events_bytes: bytes, policy_bytes: bytes | None, user: str = "anonymous") -> dict:
        limit = self.settings.max_upload_bytes
        if len(events_bytes) > limit or (policy_bytes and len(policy_bytes) > limit):
            raise InvalidRequest(f"upload exceeds the {limit:,}-byte limit")
        tmp = self.settings.state_dir / "uploads" / new_id()
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            (tmp / "events.jsonl").write_bytes(events_bytes)
            if policy_bytes:
                (tmp / "policy.json").write_bytes(policy_bytes)
            try:
                d = ds.register(self.store, self.settings, name[:120] or "Uploaded dataset", "uploaded", tmp / "events.jsonl",
                                tmp / "policy.json" if policy_bytes else None, f"uploaded by {user}")
            except ds.DatasetError as exc:
                raise InvalidRequest(str(exc)) from exc
        finally:
            for f in tmp.glob("*"):
                f.unlink()
            tmp.rmdir()
        return d

    # ------------------------------------------------------------------------------ overview
    def overview(self) -> dict:
        rules = self.list_rule_versions(("draft", "approved", "paused"))
        by_state: dict = {}
        for r in self.store.q("SELECT state, COUNT(*) AS n FROM rule_versions GROUP BY state"):
            by_state[r["state"]] = r["n"]
        recent = [{k: v for k, v in self.get_run(r["id"]).items() if k != "run_dir"} for r in self.store.q("SELECT id FROM runs ORDER BY created_at DESC LIMIT 8")]
        health = []
        for rv in rules:
            if rv["state"] == "paused":
                pr = rv.get("pause_reason") or {}
                health.append({"severity": "violation", "kind": "rule-paused", "ref": rv["id"],
                               "message": f"{rv['name']} (v{rv['version']}) is paused: "
                                          + "; ".join(bc["condition"] for bc in pr.get("blockedConditions", [])[:2]) + " cannot be evaluated."})
        for d in ds.list_all(self.store):
            latest = self.store.one("SELECT summary FROM runs WHERE dataset_id=? AND state='completed' ORDER BY created_at DESC LIMIT 1", (d["id"],))
            counts = ((loads(latest["summary"]) or {}).get("counts") or {}) if latest else {}
            if counts.get("quarantined"):
                health.append({"severity": "uncertain", "kind": "quarantined-rows", "ref": d["id"],
                               "message": f"{counts['quarantined']} row(s) in {d['name']} were quarantined in the latest run (malformed timestamps / missing keys)."})
            if counts.get("duplicatesDropped"):
                health.append({"severity": "uncertain", "kind": "duplicates", "ref": d["id"],
                               "message": f"{counts['duplicatesDropped']} redelivered event(s) in {d['name']} were collapsed."})
            ins = (counts.get("resultsByStatus") or {}).get("insufficient_context", 0)
            if ins:
                health.append({"severity": "uncertain", "kind": "insufficient-context", "ref": d["id"],
                               "message": f"{ins} result(s) could not be decided (no policy record or unobserved value) in the latest run on {d['name']}."})
            unreliable = [c for c, meta in _unreliable_cols().items() if c in d["profile"]["columns"]]
            if unreliable:
                health.append({"severity": "uncertain", "kind": "unreliable-field", "ref": d["id"],
                               "message": f"{d['name']} carries {', '.join(unreliable)}, documented as unreliable; no compiled rule may depend on it."})
        failed = self.store.q("SELECT id, kind, ref FROM jobs WHERE state='failed' ORDER BY updated_at DESC LIMIT 3")
        for j in failed:
            health.append({"severity": "violation", "kind": "job-failed", "ref": j["id"], "message": f"A {j['kind']} job failed (see job {j['id'][:8]})."})
        return {"ruleCounts": by_state,
                "activeRules": [r for r in rules if r["state"] in ("approved", "draft")][:12],
                "pausedRules": [r for r in rules if r["state"] == "paused"],
                "recentRuns": recent, "dataHealth": health,
                "datasets": [{k: d[k] for k in ("id", "name", "kind", "version", "rowCount", "fingerprint")} for d in ds.list_all(self.store)],
                "engine": {"selected": engines.choose(self.settings.engine), "spark": engines.spark_available()[0],
                           "sparkNote": engines.spark_available()[1]},
                "extractors": [e.to_dict() for e in extractors.list_extractors()]}


def _unreliable_cols() -> dict:
    from ..validation import _log_schema
    return {k: v for k, v in _log_schema().items() if v.get("observability") == "unreliable"}
