import { api, pollJob } from "../api.js";
import { banner, chip, clear, empty, fmt, h, summarizeRule, table, toast } from "../ui.js";

// Validation review: what the report says vs. what the data can support, actionable errors, the rule's lifecycle
// (refine / run / approve), and what happens to rules when a dataset's schema changes.

const ADVICE = {
  WINDOW_MISSING: "Add an explicit time window to the report (for example “within 5 minutes”) and analyze again.",
  WINDOW_CONFLICT: "The report gives two different windows for one rule. Decide which one is intended and remove the other.",
  WINDOW_AMBIGUOUS: "Several durations appear; state the detection window in the sentence that states the threshold.",
  WINDOW_OUTSIDE_RULE_SENTENCE: "Put the window in the same sentence as the threshold so the pairing is unambiguous.",
  WINDOW_MODEL_DISAGREES: "The extractor and the passage disagree. The passage wins in the stored rule, but a person should confirm which is meant.",
  COUNT_MISSING: "State a minimum number (for example “5 or more”) in the sentence that states the rule.",
  COUNT_CONFLICT: "The report gives two different thresholds. Keep one.",
  COUNT_UNKNOWN_OBJECT: "The report counts something no supported behaviour counts. This detection cannot be compiled by this system.",
  COUNT_SEMANTICS_MISMATCH: "What the report counts (events, distinct accounts, distinct hosts) does not match the chosen behaviour.",
  UNSUPPORTED_QUALIFIER: "The report adds a condition no supported recipe can evaluate. Compiling anyway would silently drop it, so it is refused.",
  UNKNOWN_FIELD: "The report needs a field the authentication log does not provide. Add that data source first.",
  UNMAPPED_FIELD_PHRASE: "The report asks for something that could not be mapped to a log field; name the field explicitly.",
  UNDERSPECIFIED_BEHAVIOUR: "The report resembles a supported behaviour but omits required numbers. Add them and analyze again.",
  DIRECTION_SPECIFIC_METHOD: "This behaviour fires on any mismatch; a one-directional policy would over-alert.",
  BEHAVIOUR_CONFLICT: "The extractor and the passage point at different behaviours; a person should decide.",
  UNSUPPORTED_BEHAVIOUR: "No supported behaviour matches this report.",
  PROMPT_INJECTION_SUSPECTED: "The text contains instruction-like content. It was not sent to any LLM.",
};

function dependencyTable(deps) {
  return table([
    { label: "Field", render: (d) => h("code", {}, d.field) },
    { label: "Why the rule needs it", render: (d) => h("span", { class: "small" }, d.role) },
    { label: "Expected", render: (d) => d.expectedType },
    { label: "Data provides", render: (d) => d.actualType || "—" },
    { label: "Status", render: (d) => chip(d.status), },
  ], deps);
}

function conditionList(conds) {
  return h("ul", { class: "plain" }, conds.map((c) => h("li", {},
    c.because ? chip("missing", "Cannot be evaluated") : chip("supported", "Evaluable"), " ", c.condition,
    c.because ? h("div", { class: "small muted" }, c.because.map((b) => `${b.field}: ${b.status === "wrong_type" ? `expected ${b.expectedType}, data has ${b.actualType}` : b.status === "missing" ? "not present in the data" : b.status}`).join("; ")) : null)));
}

// ---- rule lifecycle ---------------------------------------------------------------------------
async function rulePanel(a, datasets, rerender) {
  const rvs = a.ruleVersions || [];
  if (!rvs.length) return null;
  const rv = await api(`/api/rule-versions/${rvs[rvs.length - 1].id}`);
  const datasetSel = h("select", { "aria-label": "Dataset to run on" }, datasets.map((d) => h("option", { value: d.id }, `${d.name} (v${d.version})`)));
  const status = h("div", {});
  const runBtn = h("button", { class: "primary", disabled: !["draft", "approved"].includes(rv.state), onclick: async () => {
    runBtn.disabled = true;
    clear(status).append(h("div", {}, h("span", { class: "spin" }), "Queued…"));
    try {
      const r = await api(`/api/rule-versions/${rv.id}/runs`, { method: "POST", body: { datasetId: datasetSel.value } });
      const job = await pollJob(r.jobId, (j) => clear(status).append(h("div", {}, chip(j.state), h("span", { class: "muted small" }, `  run ${fmt.short(r.runId)} on the ${r.engine === "spark" ? "Spark" : "Python reference"} engine`))));
      if (job.state === "completed") { toast("Run completed."); location.hash = `#/investigation/${r.runId}`; }
      else { clear(status).append(banner("bad", "The run failed", job.error?.message || "See the job history.")); runBtn.disabled = false; }
    } catch (e) {
      runBtn.disabled = false;
      clear(status).append(e.body?.dependencies ? banner("bad", "Not run: the data cannot evaluate this rule", h("div", {}, e.message, h("ul", { class: "plain" }, e.body.dependencies.map((d) => h("li", {}, `${d.field}: ${d.status}${d.detail ? " — " + d.detail : ""}`))))) : banner("bad", "Could not start the run", e.message));
    }
  } }, "Run on dataset");

  // refine
  const cnt = h("input", { type: "number", min: "1", step: "1", placeholder: String(rv.compiled.countThreshold ?? rv.compiled.distinctThreshold ?? ""), "aria-label": "New count", style: "width:110px", disabled: rv.compiled.recipe === "PolicyCompare" });
  const win = h("input", { type: "number", min: "1", step: "1", placeholder: String(rv.compiled.timeWindowSeconds ?? ""), "aria-label": "New window in seconds", style: "width:130px", disabled: rv.compiled.recipe === "PolicyCompare" });
  const note = h("input", { type: "text", placeholder: "Why? (required)", "aria-label": "Reason", style: "min-width:220px;flex:1" });
  const refine = h("button", { disabled: rv.compiled.recipe === "PolicyCompare", onclick: async () => {
    const overrides = {};
    if (cnt.value !== "") overrides.count = Number(cnt.value);
    if (win.value !== "") overrides.windowSeconds = Number(win.value);
    try { const n = await api(`/api/rule-versions/${rv.id}/refine`, { method: "POST", body: { overrides, note: note.value } }); toast(`Created v${n.version} (analyst-refined).`); rerender(); }
    catch (e) { toast(e.body?.issues?.length ? e.body.issues.map((i) => i.message).join(" ") : e.message, "bad"); }
  } }, "Create refined version");

  // approve
  const completed = rv.runs.filter((r) => r.state === "completed" && r.purpose === "execute");
  const runSel = h("select", { "aria-label": "Run reviewed" }, completed.map((r) => h("option", { value: r.id }, `run ${fmt.short(r.id)} · ${fmt.n(r.counts?.alertsWritten)} results`)));
  const appNote = h("input", { type: "text", placeholder: "Approval note", "aria-label": "Approval note", style: "min-width:220px;flex:1" });
  const approve = h("button", { class: "primary", disabled: rv.state !== "draft" || !completed.length, onclick: async () => {
    try { await api(`/api/rule-versions/${rv.id}/decision`, { method: "POST", body: { decision: "approve", runId: runSel.value, note: appNote.value } }); toast("Approved."); rerender(); }
    catch (e) { toast(e.message, "bad"); }
  } }, "Approve this version");
  const reject = h("button", { disabled: !["draft", "approved", "paused"].includes(rv.state), onclick: async () => {
    try { await api(`/api/rule-versions/${rv.id}/decision`, { method: "POST", body: { decision: "reject", note: appNote.value } }); toast("Retired."); rerender(); }
    catch (e) { toast(e.message, "bad"); }
  } }, "Retire");

  const sigma = await api(`/api/rule-versions/${rv.id}/sigma`);
  return h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, `Rule version ${rv.version}`), chip(rv.state)),
    h("dl", { class: "kv" },
      h("dt", {}, "Detects"), h("dd", {}, rv.behaviour.name),
      h("dt", {}, "Compiled as"), h("dd", {}, h("code", {}, summarizeRule(rv.compiled))),
      h("dt", {}, "Rule hash"), h("dd", {}, h("code", {}, rv.rule_hash), ` · compiler ${rv.compiler_version}`),
      h("dt", {}, "Origin"), h("dd", {}, rv.origin === "analyst-refined" ? h("span", {}, chip("uncertain", "Analyst-refined"), ` ${rv.overrides.changed.join(", ")} changed from the report's value (${JSON.stringify(rv.overrides.from)} → ${JSON.stringify(rv.overrides.to)}): “${rv.overrides.note}”`) : "Built server-side from the stored, evidence-backed analysis")),
    rv.state === "paused" ? banner("bad", "Paused", h("div", {}, (rv.pause_reason?.blockedConditions || []).map((b) => h("div", {}, `${b.condition} — ${b.because.map((x) => `${x.field} ${x.status}`).join(", ")}`)))) : null,
    h("h3", { style: "margin-top:14px" }, "Run it"), h("div", { class: "row" }, datasetSel, runBtn), h("div", { style: "margin-top:8px" }, status),
    rv.runs.length ? h("p", { class: "small muted", style: "margin-top:8px" }, "Previous runs: ", rv.runs.slice(0, 5).map((r) => h("a", { href: `#/investigation/${r.id}`, style: "margin-right:8px" }, `${fmt.short(r.id)} (${r.state})`))) : null,
    h("h3", { style: "margin-top:14px" }, "Refine a number"),
    h("p", { class: "small muted" }, "The rule is rebuilt on the server and strictly re-validated. The change is recorded as NOT backed by the report."),
    h("div", { class: "row" }, h("span", { class: "small muted" }, "count"), cnt, h("span", { class: "small muted" }, "window (s)"), win, note, refine),
    h("h3", { style: "margin-top:14px" }, "Decision"),
    completed.length ? null : h("p", { class: "small muted" }, "Approval needs a completed run of exactly this version, so you approve something you have seen work."),
    h("div", { class: "row" }, runSel, appNote, approve, reject),
    rv.decisions.length ? h("ul", { class: "plain small", style: "margin-top:8px" }, rv.decisions.map((d) => h("li", {}, `${fmt.time(d.created_at)} · ${d.analyst} · ${d.decision}${d.note ? ` — ${d.note}` : ""}`))) : null,
    h("h3", { style: "margin-top:14px" }, "Sigma export"),
    sigma.status === "rejected" ? banner("warn", "Not exported", sigma.reason)
      : h("div", {}, banner("info", "Exported with declared differences", h("ul", { class: "plain small" }, sigma.notPreserved.map((n) => h("li", {}, n)))), h("pre", { class: "cmd" }, sigma.sigma)));
}

// ---- schema-change impact -----------------------------------------------------------------------
async function schemaPanel(datasets, rerender) {
  const box = h("div", { class: "card" });
  const d = datasets[0];
  if (!d) return box.appendChild(empty("No dataset registered")) && box;
  const full = await api(`/api/datasets/${d.id}`);
  const cols = Object.entries(full.profile.columns).map(([k, v]) => ({ name: k, type: v, policy: false }))
    .concat(Object.entries(full.profile.policyColumns).map(([k, v]) => ({ name: `policy.${k}`, type: v, policy: true })));
  const dropped = new Set();
  const retyped = {};
  const out = h("div", { style: "margin-top:12px" });
  const colTable = h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Column", "Type", "Simulate"].map((t) => h("th", {}, t)))),
    h("tbody", {}, cols.map((c) => h("tr", {}, h("td", {}, h("code", {}, c.name)), h("td", {}, c.type),
      h("td", {}, h("label", { style: "display:inline-flex;gap:6px;align-items:center;margin:0 12px 0 0" }, h("input", { type: "checkbox", "aria-label": `Remove ${c.name}`, onchange: (e) => e.target.checked ? dropped.add(c.name) : dropped.delete(c.name) }), "remove"),
        c.type === "boolean" || c.type === "string" ? h("label", { style: "display:inline-flex;gap:6px;align-items:center;margin:0" }, h("input", { type: "checkbox", "aria-label": `Change ${c.name} to a different type`, onchange: (e) => { if (e.target.checked) retyped[c.name] = c.type === "boolean" ? "string" : "long"; else delete retyped[c.name]; } }), c.type === "boolean" ? "→ string" : "→ number") : null))))));
  const apply = h("button", { class: "primary", onclick: async () => {
    if (!dropped.size && !Object.keys(retyped).length) { toast("Tick a column to remove or retype first.", "bad"); return; }
    try {
      const imp = await api(`/api/datasets/${d.id}/schema-change`, { method: "POST", body: { drop: [...dropped], retype: retyped } });
      clear(out).append(impactView(imp, rerender));
      toast(`Dataset is now version ${imp.toVersion}.`);
    } catch (e) { toast(e.message, "bad"); }
  } }, "Apply schema change");
  const restore = h("button", { onclick: async () => {
    try { const imp = await api(`/api/datasets/${d.id}/restore`, { method: "POST", body: { version: 1 } }); clear(out).append(impactView(imp, rerender)); toast(`Restored the original schema as version ${imp.toVersion}. Paused rules stay paused until revalidated.`); }
    catch (e) { toast(e.message, "bad"); }
  } }, "Restore original schema");
  box.append(h("div", { class: "card-head" }, h("h2", {}, "Schema-change impact"), chip(d.kind === "demonstration" ? "demonstration" : "fresh", `${d.name} · version ${full.version}`)),
    h("p", { class: "muted" }, "Remove or retype a column and see exactly which rules can no longer be evaluated, and why. Affected rules are paused — never quietly weakened — and unaffected rules keep running. A paused rule resumes only after the data is fixed and a revalidation run succeeds."),
    colTable, h("div", { class: "row", style: "margin-top:10px" }, apply, restore), out);
  return box;
}

function impactView(imp, rerender) {
  const one = (r, paused) => h("div", { class: "cond" },
    h("div", { class: "row" }, h("strong", {}, r.ruleName), h("span", { class: "muted small" }, `v${r.version}`), h("span", { class: "spacer" }), paused ? chip("paused") : chip("supported", "Keeps running")),
    paused ? h("div", { style: "margin-top:6px" }, conditionList(r.blockedConditions), r.evaluableConditions?.length ? h("details", {}, h("summary", {}, `${r.evaluableConditions.length} other condition(s) are still evaluable`), conditionList(r.evaluableConditions)) : null) : null);
  return h("div", { class: "stack" },
    banner(imp.summary.affected ? "bad" : "ok", `${imp.summary.affected} rule(s) affected · ${imp.summary.unaffected} unaffected${imp.summary.resumable ? ` · ${imp.summary.resumable} can be revalidated` : ""}`,
      `Dataset version ${imp.fromVersion} → ${imp.toVersion}. ${imp.change?.drop?.length ? `Removed: ${imp.change.drop.join(", ")}. ` : ""}${imp.change?.retype && Object.keys(imp.change.retype).length ? `Retyped: ${Object.entries(imp.change.retype).map(([k, v]) => `${k} → ${v}`).join(", ")}. ` : ""}${imp.change?.restoredFrom ? `Restored from version ${imp.change.restoredFrom}.` : ""}`),
    imp.affected.map((r) => one(r, true)), imp.resumable.map((r) => h("div", { class: "cond" }, h("div", { class: "row" }, h("strong", {}, r.ruleName), h("span", { class: "spacer" }), chip("paused"), h("button", { class: "primary", onclick: () => resume(r.ruleVersionId, rerender) }, "Revalidate and resume")))),
    imp.unaffected.map((r) => one(r, false)));
}

async function resume(rvId, rerender) {
  try {
    const r = await api(`/api/rule-versions/${rvId}/resume`, { method: "POST" });
    toast("Revalidation run started…");
    const job = await pollJob(r.jobId);
    toast(job.state === "completed" ? "Revalidated on current data — the rule is back." : "Revalidation failed; the rule stays paused.", job.state === "completed" ? "" : "bad");
    rerender();
  } catch (e) { toast(e.body?.dependencies ? `Still blocked: ${e.body.dependencies.map((d) => `${d.field} ${d.status}`).join(", ")}` : e.message, "bad"); }
}

export async function validation({ params, navigate }) {
  const [ds, overview] = await Promise.all([api("/api/datasets"), api("/api/overview")]);
  const datasets = ds.datasets;
  const root = h("div", { class: "stack" });
  const head = h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Validation review"),
    h("p", {}, "What the report says, what the data can support, and what to do about anything that cannot be compiled.")));
  root.append(head);

  if (params[0]) {
    const a = await api(`/api/analyses/${params[0]}`);
    const res = a.result || {};
    const rec = res.reconciliation || {};
    const ds0 = datasets.find((d) => d.id === (res.dataset?.id)) || datasets[0];
    const sup = await api(`/api/analyses/${a.id}/data-support?datasetId=${encodeURIComponent(ds0.id)}`);
    const stateBanner = { ready: banner("ok", "Supported", "Every condition is evidenced in the report and every field the rule reads or emits is present, correctly typed, and reliable in the selected dataset."),
      needs_review: banner("warn", "Needs review", "Evidence is missing or contradictory; nothing was compiled."),
      rejected: banner("bad", res.rejectionKind === "data" ? "Rejected: the data cannot support this report" : "Rejected", "Nothing was compiled."),
      failed: banner("bad", "Analysis failed", res.error?.message) }[a.status];
    root.append(h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, a.report.title), h("a", { class: "btn", href: `#/workspace/${a.id}` }, "Open in Report workspace")), stateBanner));
    if ((rec.reasons || []).length) {
      root.append(h("div", { class: "card" }, h("h2", {}, "Actionable errors"), h("div", { class: "stack" }, rec.reasons.map((r) => h("div", { class: "cond" },
        h("div", { class: "row" }, chip(r.severity === "reject" ? "rejected" : "needs_review", r.code)), h("p", { style: "margin:6px 0 2px" }, r.message),
        (r.evidence || []).slice(0, 2).map((e) => h("div", { class: "quote" }, `“${e.quote}”`)),
        h("p", { class: "small", style: "margin:4px 0 0" }, h("strong", {}, "What to do: "), ADVICE[r.code] || "Review the cited passage."))))));
    }
    if (rec.behaviourId) {
      root.append(h("div", { class: "grid split" },
        h("div", { class: "card" }, h("h2", {}, "Conditions of the rule"), h("p", { class: "small muted" }, `Checked against ${sup.dataset.name} (version ${sup.dataset.version}).`), conditionList(sup.conditions)),
        h("div", { class: "card" }, h("h2", {}, "Data dependencies"), dependencyTable(sup.dependencies))));
    }
    if (a.status === "needs_review" || a.status === "rejected") {
      root.append(h("div", { class: "card" }, h("button", { onclick: () => { try { sessionStorage.setItem("sf.prefill", a.report.text); } catch { /* ignore */ } navigate("#/workspace"); } }, "Edit the report and analyze again")));
    }
    const rp = await rulePanel(a, datasets, () => location.reload());
    if (rp) root.append(rp);
  } else {
    root.append(banner("info", "Pick something to review", h("span", {}, "Open an analysis from the ", h("a", { href: "#/workspace" }, "Report workspace"), ", or explore what a schema change does to your rules below.")));
  }

  root.append(await schemaPanel(datasets, () => location.reload()));
  if (overview.pausedRules.length) {
    root.append(h("div", { class: "card" }, h("h2", {}, "Paused rules"), table([
      { label: "Rule", render: (r) => r.name }, { label: "Why", render: (r) => (r.pause_reason?.blockedConditions || []).map((b) => b.condition).slice(0, 2).join("; ") },
      { label: "", render: (r) => h("button", { onclick: () => resume(r.id, () => location.reload()) }, "Revalidate and resume") }], overview.pausedRules)));
  }
  return root;
}
