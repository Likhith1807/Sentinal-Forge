import { api } from "../api.js";
import { banner, chip, clear, empty, fmt, h, summarizeRule, table } from "../ui.js";

// Investigation: pick an alert, then follow its reasoning back to the report — the six questions of an evidence record.

const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, text) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

// Incident timeline: the supporting events between the window start and the alert instant.
function timeline(rec) {
  const evs = rec.fired.supportingEvents || [];
  if (!evs.length || !rec.fired.windowStart) return null;
  const t0 = Date.parse(rec.fired.windowStart), t1 = Date.parse(rec.alert.detectedAt);
  const span = Math.max(1, t1 - t0);
  const W = 640, pad = 18;
  const x = (t) => pad + ((t - t0) / span) * (W - 2 * pad);
  const root = svg("svg", { class: "timeline", viewBox: `0 0 ${W} 74`, role: "img", "aria-label": `Timeline of ${evs.length} supporting events between ${rec.fired.windowStart} and ${rec.alert.detectedAt}` });
  root.append(svg("line", { x1: pad, x2: W - pad, y1: 34, y2: 34, stroke: "currentColor", "stroke-opacity": ".25", "stroke-width": 2 }));
  evs.forEach((e) => {
    const cx = Math.min(W - pad, Math.max(pad, x(Date.parse(e.timestamp))));
    const c = svg("circle", { cx, cy: 34, r: 6, fill: "var(--series-1)", stroke: "var(--surface)", "stroke-width": 2 });
    c.append(svg("title", {}, `${e.eventId} · ${e.timestamp} · ${e.value ?? ""}`));
    root.append(c);
  });
  root.append(svg("rect", { x: W - pad - 6, y: 25, width: 12, height: 12, fill: "var(--series-2)", stroke: "var(--surface)", "stroke-width": 2, transform: `rotate(45 ${W - pad} 31)` }));
  root.append(svg("text", { x: pad, y: 62, "text-anchor": "start" }, `window start ${rec.fired.windowStart.slice(11, 23)}`));
  root.append(svg("text", { x: W - pad, y: 62, "text-anchor": "end" }, `alert ${rec.alert.detectedAt.slice(11, 23)}`));
  root.append(svg("text", { x: W / 2, y: 14, "text-anchor": "middle" }, `● supporting event (${evs.length})   ◆ alert`));
  return root;
}

function passage(report, p) {
  const sent = report.text.slice(p.sentenceStart, p.sentenceEnd);
  const a = p.start - p.sentenceStart, b = p.end - p.sentenceStart;
  return h("div", { class: "quote" }, sent.slice(0, a), h("mark", { style: "background:var(--hl-count);color:inherit;border-radius:3px;padding:0 2px" }, sent.slice(a, b)), sent.slice(b),
    h("div", { class: "small faint" }, `${p.role} · characters ${p.start}–${p.end} · `, p.quoteVerified ? chip("verified") : chip("violation", "Quote NOT found at these offsets")));
}

function evidencePanel(rec, report, run) {
  const sec = (q, sub, body) => h("div", { class: "ev-section" }, h("h3", {}, h("span", { class: "ev-q" }, q, " ", h("small", {}, sub))), body);
  const tl = timeline(rec);
  return h("div", { class: "card", id: "evidence" },
    h("div", { class: "card-head" }, h("h2", {}, "Evidence record"), chip(rec.alert.status)),
    h("p", { class: "small muted" }, "A record of where this alert came from — not a proof that the detection is correct. Passages are quote-verified only."),
    sec("Why was this rule created?", "the report passage", h("div", {},
      h("p", { class: "small" }, "From ", h("a", { href: `#/workspace/${run.ruleVersion.analysis_id}` }, rec.why.report.title), ` · sha256 ${rec.why.report.sha256.slice(0, 12)}…`),
      rec.why.passages.length ? rec.why.passages.map((p) => passage(report, p)) : h("p", { class: "muted" }, "No passages were stored for this rule."))),
    sec("What does it mean?", "normalised conditions", h("div", {}, h("p", {}, h("strong", {}, rec.means.behaviour.name), " — ", rec.means.behaviour.checks),
      h("ul", { class: "plain" }, rec.means.conditions.map((c) => h("li", {}, h("strong", {}, c.name), ": ", c.plain, c.semantics ? h("span", { class: "muted" }, `  [${c.semantics.replace("_", " ")}]`) : null))))),
    sec("Can our data support it?", "required fields and policy", h("details", { open: !rec.supported.allSupported }, h("summary", {}, rec.supported.allSupported ? `Yes — all ${rec.supported.dependencies.length} fields are present, correctly typed and reliable` : "No — see the failing fields"), table([
      { label: "Field", render: (d) => h("code", {}, d.field) }, { label: "Used for", render: (d) => h("span", { class: "small" }, d.role) },
      { label: "Type", render: (d) => `${d.actualType || "—"} (needs ${d.expectedType})` }, { label: "", render: (d) => chip(d.status) }], rec.supported.dependencies),
      h("p", { class: "small muted", style: "margin-top:6px" }, `${rec.supported.dataset.name} · version ${rec.supported.dataset.version} · fingerprint ${rec.supported.dataset.fingerprint}`))),
    sec("Why did this alert fire?", "supporting events, times, values", h("div", {}, h("p", {}, rec.fired.explanation), tl,
      rec.fired.supportingEvents.length ? table([{ label: "Event", render: (e) => h("code", {}, e.eventId) }, { label: "Timestamp", render: (e) => h("code", {}, e.timestamp) }, { label: "Value", render: (e) => e.value ?? "" }], rec.fired.supportingEvents) : (rec.fired.observedValue !== undefined ? h("dl", { class: "kv" }, h("dt", {}, "Observed"), h("dd", {}, h("code", {}, String(rec.fired.observedValue))), h("dt", {}, "Policy expects"), h("dd", {}, h("code", {}, String(rec.fired.expectedValue ?? "—")))) : null))),
    sec("What was actually executed?", "rule version, compiler, run", h("dl", { class: "kv" },
      h("dt", {}, "Rule version"), h("dd", {}, `v${rec.executed.ruleVersion} (${rec.executed.origin})`, " · hash ", h("code", {}, rec.executed.ruleHash)),
      h("dt", {}, "Compiler"), h("dd", {}, rec.executed.compilerVersion), h("dt", {}, "Run"), h("dd", {}, h("code", {}, rec.executed.runId)),
      h("dt", {}, "Engine"), h("dd", {}, `${rec.executed.engine}${rec.executed.sparkVersion ? ` (Spark ${rec.executed.sparkVersion})` : ""}`),
      h("dt", {}, "Compiled rule"), h("dd", {}, h("details", {}, h("summary", {}, "show JSON"), h("pre", { class: "cmd" }, JSON.stringify(rec.executed.compiled, null, 2)))))),
    sec("What remains uncertain?", "missing information and known limits", h("div", {}, h("ul", { class: "plain" }, rec.uncertain.items.map((u) => h("li", {}, u))),
      rec.uncertain.limitations.length ? h("p", { class: "small muted", style: "margin-top:6px" }, h("strong", {}, "Known limitations of this behaviour: "), rec.uncertain.limitations.join(" ")) : null)));
}

async function detail(runId, alertId, navigate) {
  const run = await api(`/api/runs/${runId}`);
  const [alerts, report] = await Promise.all([api(`/api/runs/${runId}/alerts?limit=500`), api(`/api/reports/${run.report.id}`)]);
  const counts = run.summary?.counts || {};
  const panel = h("div", {}, empty("Select an alert", "Click any row to follow its reasoning back to the report."));
  const load = async (id) => {
    clear(panel).append(h("div", { class: "state" }, h("span", { class: "spin" }), "Loading evidence record…"));
    try { clear(panel).append(evidencePanel(await api(`/api/runs/${runId}/alerts/${encodeURIComponent(id)}/evidence`), report, run)); }
    catch (e) { clear(panel).append(banner("bad", "Could not load the evidence record", e.message)); }
    document.querySelectorAll("tbody tr[aria-selected]").forEach((r) => r.removeAttribute("aria-selected"));
    document.querySelector(`tbody tr[data-id="${CSS.escape(id)}"]`)?.setAttribute("aria-selected", "true");
  };
  const tbl = table([
    { label: "Result", render: (a) => chip(a.status) },
    { label: run.ruleVersion.behaviour.id === "auth-method-policy-violation" || run.ruleVersion.behaviour.id === "mfa-missing-on-required-account" ? "Account" : "Group", render: (a) => h("code", {}, a.groupKey) },
    { label: "At", render: (a) => h("code", {}, a.detectedAt.replace("T", " ").replace("Z", "")) },
    { label: "Detail", render: (a) => (a.matchedCount ? `${a.matchedCount} in window` : a.reason ? a.reason.replace(/_/g, " ") : "") },
  ], alerts.alerts, { emptyText: run.state === "completed" ? "This run produced no alerts — the rule matched nothing in this dataset." : "No results.", onRow: (a) => { history.replaceState(null, "", `#/investigation/${runId}/alert/${encodeURIComponent(a.triggeringEventId)}`); load(a.triggeringEventId); } });
  tbl.querySelectorAll("tbody tr").forEach((tr, i) => tr.dataset.id = alerts.alerts[i]?.triggeringEventId);
  if (alertId) load(decodeURIComponent(alertId));

  const failed = run.state === "failed";
  return h("div", { class: "stack" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Investigation"), h("p", {}, `${run.ruleVersion.name} · v${run.ruleVersion.version}`)),
      h("div", { class: "row" }, chip(run.state), chip("fresh"), run.dataset.kind === "demonstration" ? chip("demonstration") : null, run.freshness.stale ? chip("stale") : null, h("a", { class: "btn", href: "#/investigation" }, "All runs"))),
    failed ? banner("bad", "This run failed", run.error?.message || "See engine.log in the run directory (the directory was kept).") : null,
    run.freshness.stale ? banner("warn", "Data has changed since this run", "The dataset now has a newer version; these results describe the version that was read (below), not the current data.") : null,
    h("div", { class: "card" }, h("dl", { class: "kv" },
      h("dt", {}, "Rule"), h("dd", {}, h("code", {}, summarizeRule(run.ruleVersion.compiled)), ` · hash `, h("code", {}, run.ruleVersion.rule_hash)),
      h("dt", {}, "Report"), h("dd", {}, h("a", { href: `#/workspace/${run.ruleVersion.analysis_id}` }, run.report.title)),
      h("dt", {}, "Dataset read"), h("dd", {}, `${run.dataset.name} · version ${run.dataset.version} · fingerprint `, h("code", {}, run.dataset_fingerprint)),
      h("dt", {}, "Engine"), h("dd", {}, `${run.summary?.engine || run.engine}${run.summary?.sparkVersion ? ` (Spark ${run.summary.sparkVersion})` : ""} · ${run.summary?.elapsedSeconds ?? "–"} s`),
      h("dt", {}, "Events"), h("dd", {}, `${fmt.n(counts.eventsRead)} read · ${fmt.n(counts.eventsEvaluated)} evaluated · `, h("strong", {}, `${fmt.n(counts.quarantined)} quarantined`), ` (malformed / missing keys) · ${fmt.n(counts.duplicatesDropped)} duplicates collapsed`))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, `Results (${alerts.total})`),
      h("div", { class: "row small" }, Object.entries(alerts.statusCounts).map(([k, v]) => h("span", {}, chip(k), ` ${v}`)))), tbl),
    panel);
}

async function list() {
  const o = await api("/api/overview");
  return h("div", { class: "stack" }, h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Investigation"), h("p", {}, "Open a run to inspect what matched and why."))),
    h("div", { class: "card" }, table([
      { label: "Run", render: (r) => h("a", { href: `#/investigation/${r.id}` }, fmt.short(r.id)) }, { label: "Rule", render: (r) => `${r.ruleVersion.name} v${r.ruleVersion.version}` },
      { label: "Data", render: (r) => `${r.dataset.name} v${r.dataset.version}` }, { label: "State", render: (r) => chip(r.state) }, { label: "Results", num: true, render: (r) => fmt.n(r.summary?.counts?.alertsWritten) },
      { label: "Started", render: (r) => fmt.time(r.started_at) }], o.recentRuns, { emptyText: "No runs yet — create a rule from a report and run it.", onRow: (r) => { location.hash = `#/investigation/${r.id}`; } })));
}

export async function investigation({ params, navigate }) {
  return params[0] ? detail(params[0], params[1], navigate) : list();
}
