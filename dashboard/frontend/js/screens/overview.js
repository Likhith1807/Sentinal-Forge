import { api } from "../api.js";
import { banner, chip, empty, fmt, h, summarizeRule, table } from "../ui.js";

// Overview: what is running, what ran recently, and what is wrong with the data.
export async function overview({ ctx }) {
  const o = await api("/api/overview");
  const counts = o.ruleCounts || {};
  const active = (counts.draft || 0) + (counts.approved || 0);

  const stat = (n, label, tone) => h("div", { class: "card stat" }, h("div", { class: "n", style: tone ? `color:var(--${tone})` : "" }, fmt.n(n)), h("div", { class: "l" }, label));
  const health = o.dataHealth.length
    ? h("div", { class: "stack" }, o.dataHealth.map((x) => banner(x.severity === "violation" ? "bad" : "warn",
        x.severity === "violation" ? "Needs action" : "Worth knowing", x.message,
        x.kind === "rule-paused" ? h("a", { class: "btn", href: "#/validation" }, "Review") : null)))
    : banner("ok", "No data-health problems", "Every dataset is readable and no rule is paused.");

  const ruleTable = table([
    { label: "Rule", render: (r) => h("div", {}, h("div", { class: "ellip", title: r.name }, r.name), h("div", { class: "small muted" }, `v${r.version} · ${r.origin === "analyst-refined" ? "analyst-refined" : "from report evidence"}`)) },
    { label: "Detects", render: (r) => h("div", {}, r.behaviour.name, h("div", { class: "small muted mono" }, summarizeRule(r.compiled))) },
    { label: "State", render: (r) => chip(r.state) },
    { label: "Last run", render: (r) => (r.runs[0] ? h("a", { href: `#/investigation/${r.runs[0].id}` }, `${r.runs[0].state === "completed" ? `${fmt.n(r.runs[0].counts?.alertsWritten)} results` : r.runs[0].state}`) : h("span", { class: "muted" }, "never run")) },
  ], [...o.activeRules, ...o.pausedRules], { emptyText: "No rules yet.", onRow: (r) => { location.hash = r.state === "paused" ? "#/validation" : `#/validation/${r.analysis_id}`; } });

  const runTable = table([
    { label: "Run", render: (r) => h("a", { href: `#/investigation/${r.id}` }, fmt.short(r.id)) },
    { label: "Rule", render: (r) => h("span", { class: "ellip", title: r.ruleVersion.name }, `${r.ruleVersion.name} v${r.ruleVersion.version}`) },
    { label: "Data", render: (r) => h("div", {}, r.dataset.name, h("div", { class: "small muted" }, `version ${r.dataset.version}`)) },
    { label: "Provenance", render: (r) => h("div", { class: "row", style: "gap:4px" }, chip("fresh"), chip("demonstration"), r.freshness.stale ? chip("stale") : null) },
    { label: "State", render: (r) => chip(r.state) },
    { label: "Results", num: true, render: (r) => fmt.n(r.summary?.counts?.alertsWritten) },
  ], o.recentRuns, { emptyText: "No runs yet. Start from the Report workspace.", onRow: (r) => { location.hash = `#/investigation/${r.id}`; } });

  return h("div", { class: "stack" },
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Overview"), h("p", {}, "Turn a threat report into a detection you can check: every rule traces back to the report passage it came from, and every result to the run that produced it.")),
      h("a", { class: "btn primary", href: "#/workspace" }, "Analyze a report")),
    h("div", { class: "grid three" }, stat(active, "Active rules (draft or approved)"), stat(counts.paused || 0, "Paused rules", counts.paused ? "bad" : ""), stat(o.recentRuns.length, "Recent runs")),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Data health")), health),
    h("div", { class: "grid split" },
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Rules")), ruleTable),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Recent runs")), runTable)),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Datasets and engine")),
      h("dl", { class: "kv" },
        o.datasets.flatMap((d) => [h("dt", {}, d.name), h("dd", {}, chip(d.kind === "demonstration" ? "demonstration" : "fresh", d.kind === "demonstration" ? "Demonstration data (synthetic)" : d.kind), ` version ${d.version} · ${fmt.n(d.rowCount)} events · fingerprint `, h("code", {}, d.fingerprint))]),
        h("dt", {}, "Execution engine"),
        h("dd", {}, o.engine.selected === "spark" ? "Apache Spark (real executor)" : "Python reference engine", o.engine.selected !== "spark" ? h("span", { class: "muted" }, " — no JVM available here; results are labelled with the engine that produced them.") : null),
        h("dt", {}, "Extractors"),
        h("dd", {}, o.extractors.map((e) => h("span", { style: "margin-right:8px" }, chip(e.available ? "ok" : "unreliable", `${e.label}${e.available ? "" : " (unavailable)"}`)))))));
}
