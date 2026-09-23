import { api } from "../api.js";
import { banner, chip, empty, fmt, h, table } from "../ui.js";

// Evaluation: archived measurements with their provenance. Nothing on this page is computed live.

function bars(rows, { max, unit = "", fmtv = (v) => v }) {
  const m = max ?? Math.max(...rows.map((r) => r.value), 1);
  return h("div", { class: "bars", role: "list" }, rows.map((r) => h("div", { class: "barrow", role: "listitem" },
    h("span", {}, r.label), h("div", { class: "bar", "aria-hidden": "true" }, h("span", { style: `width:${Math.max(0, Math.min(100, (r.value / m) * 100))}%;background:${r.color || "var(--series-1)"}` })),
    h("span", { class: "tnum", style: "text-align:right" }, `${fmtv(r.value)}${unit}`))));
}

const SECTION_RENDER = {
  generic: (s) => h("pre", { class: "cmd" }, JSON.stringify(s.data, null, 2).slice(0, 4000)),
};

export async function evaluation() {
  const ev = await api("/api/evaluation");
  if (!ev.sections.length) return h("div", { class: "stack" }, h("div", { class: "page-head" }, h("h1", {}, "Evaluation")), empty("No archived evaluation results found", "Run the evaluation scripts described in docs/evaluation.md."));
  return h("div", { class: "stack" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Evaluation"), h("p", {}, "Every number here was measured earlier and committed to the repository; each block says on what, by which command, and whether the data was tuned against."))),
    ev.sections.map((s) => h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, s.title), h("div", { class: "row" }, chip("archived"), s.dataKind ? chip(s.dataKind === "frozen-holdout" ? "verified" : "uncertain", s.dataLabel || s.dataKind) : null)),
      s.summary ? h("p", { class: "muted" }, s.summary) : null,
      (SECTION_RENDER[s.type] || SECTION_RENDER.generic)(s),
      s.caveats?.length ? h("ul", { class: "plain small muted", style: "margin-top:8px" }, s.caveats.map((c) => h("li", {}, c))) : null,
      s.reproduce ? h("details", { style: "margin-top:8px" }, h("summary", {}, "How to reproduce"), h("pre", { class: "cmd" }, s.reproduce)) : null)));
}
