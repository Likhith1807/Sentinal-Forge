import { api } from "../api.js";
import { banner, chip, empty, fmt, h, table } from "../ui.js";

// Evaluation: archived measurements with their provenance. Nothing on this page is computed live.
// Every block says WHAT was measured, on WHICH data, whether that data was tuned against, and how to reproduce it.

const pct = (x) => (x === null || x === undefined ? "–" : `${(x * 100).toFixed(x === 1 || x === 0 ? 0 : 1)}%`);
const ci = (m) => (m && m.ci95 ? `${pct(m.ci95[0])}–${pct(m.ci95[1])}` : "");

function bars(rows, { max = 1, fmtv = pct } = {}) {
  return h("div", { class: "bars", role: "list" }, rows.map((r) => h("div", { class: "barrow", role: "listitem" },
    h("span", { title: r.title || "" }, r.label),
    h("div", { class: "bar", "aria-hidden": "true" }, h("span", { style: `width:${Math.max(0, Math.min(100, (r.value / max) * 100))}%;background:${r.color || "var(--series-1)"}` })),
    h("span", { class: "tnum", style: "text-align:right" }, fmtv(r.value)))));
}

const legend = (items) => h("div", { class: "legend" }, items.map(([c, t]) => h("span", {}, h("i", { style: `background:${c};border-color:${c}` }), t)));

// ---- audit: the fine-tuned pipeline before and after the fixes -------------------------------------
function audit(s) {
  const before = s.data.before, after = s.data.after;
  const b = (before.counts || before.summary || {});
  const a = after.summary || {};
  const rows = [
    { label: "Before the fixes (legacy path, measured)", correct: b.correct, silent: (b["wrong-spec-silent"] || 0) + (b["silently-accepted"] || 0), review: b["rejected-supported"], note: "Reproduced on the 44-report regression split: the audit's 27 of 40 correct and 7 silent failures (5 wrong rules + 2 unsupported reports accepted); 8 more supported reports were refused for an incomplete field list." },
    { label: "Fine-tuned, unit read from text (no evidence check)", correct: (a["finetuned-raw"] || {}).correct, silent: ((a["finetuned-raw"] || {})["wrong-rule-silent"] || 0) + ((a["finetuned-raw"] || {})["silently-accepted"] || 0) },
    { label: "Fine-tuned + evidence check (product)", correct: (a["finetuned+evidence"] || {}).correct, silent: ((a["finetuned+evidence"] || {})["wrong-rule-silent"] || 0) + ((a["finetuned+evidence"] || {})["silently-accepted"] || 0), review: (a["finetuned+evidence"] || {})["needs-review"] },
    { label: "Evidence check only (no model)", correct: (a["evidence-only"] || {}).correct, silent: ((a["evidence-only"] || {})["wrong-rule-silent"] || 0) + ((a["evidence-only"] || {})["silently-accepted"] || 0), review: (a["evidence-only"] || {})["needs-review"] },
  ];
  return h("div", {},
    legend([["var(--series-1)", "compiled the correct rule (of 40 supported)"], ["var(--bad)", "silent failures (wrong or unsupported rule compiled)"], ["var(--warn)", "sent to review instead"]]),
    h("div", { class: "stack" }, rows.map((r) => h("div", {}, h("div", { class: "small", style: "margin-bottom:3px" }, r.label),
      bars([{ label: "correct", value: r.correct ?? 0, color: "var(--series-1)" }, { label: "silent failures", value: r.silent ?? 0, color: "var(--bad)" }, ...(r.review !== undefined ? [{ label: "needs review", value: r.review, color: "var(--warn)" }] : [])], { max: 44, fmtv: (v) => String(v) }),
      r.note ? h("div", { class: "small muted" }, r.note) : null))));
}

// ---- frozen holdout ------------------------------------------------------------------------------------
function holdout(s) {
  const sum = s.data.summary;
  if (!sum) return empty("Not run yet", "Run experiments/holdout/run_eval.py");
  const sys = sum.systems;
  const cell = (m, bad) => (m ? h("div", {}, h("span", { style: bad && m.value > 0 ? "color:var(--bad);font-weight:600" : "font-weight:600" }, pct(m.value)), h("div", { class: "small faint" }, `${ci(m)} · n=${m.n}`)) : "–");
  const rows = Object.entries(sys).map(([name, r]) => ({ name, ...r }));
  const t = table([
    { label: "System", render: (r) => h("div", {}, h("strong", {}, r.name), r.coverage < 1 ? h("div", { class: "small", style: "color:var(--warn)" }, `partial: ${r.reportsRequested - r.unavailable} of ${r.reportsRequested} reports scored (provider quota)`) : null) },
    { label: "Complete rule correct", render: (r) => cell(r.completeSpecCorrect) },
    { label: "Wrong rule, silently", render: (r) => cell(r.wrongRuleSilently, true) },
    { label: "Supported, not compiled", render: (r) => cell(r.supportedNotCompiled) },
    { label: "Unsupported accepted", render: (r) => cell(r.unsupportedAccepted, true) },
    { label: "Behaviour", render: (r) => cell(r.behaviour) },
    { label: "Count value", render: (r) => cell(r.countValue) },
    { label: "Window (s)", render: (r) => cell(r.windowSeconds) },
    { label: "Downstream P / R", render: (r) => h("span", { class: "tnum" }, `${r.downstream.precision ?? "–"} / ${r.downstream.recall ?? "–"}`) },
  ], rows);
  const prod = sys["evidence-only"] || sys["finetuned+evidence"];
  const byClass = prod ? table([{ label: "Must-not-compile class", render: (r) => r[0] }, { label: "Reports", num: true, render: (r) => r[1].n }, { label: "Wrongly compiled", num: true, render: (r) => h("span", { style: r[1].accepted ? "color:var(--bad);font-weight:600" : "" }, r[1].accepted) }],
    Object.entries(prod.falseAcceptByReasonClass)) : null;
  return h("div", { class: "stack" },
    h("p", { class: "small muted" }, `${sum.holdout.reports} reports, frozen ${sum.holdout.frozen} (git tag ${sum.holdout.tag}); ${sum.llmModel ? `LLM rows use ${sum.llmModel}.` : "No LLM rows were run on this holdout."} ${prod ? `${prod.supportedReports} must-compile · ${prod.mustNotCompileReports} must-not-compile. Product path = ${sys["evidence-only"] ? "evidence-only (the default extractor)" : "fine-tuned + evidence"}.` : ""} Intervals are 95% bootstrap over reports — with n this small they are wide.`),
    t,
    prod ? h("div", { class: "grid two" }, h("div", {}, h("h3", {}, "Product path, by kind of report it must refuse"), byClass),
      h("div", {}, h("h3", {}, "Status of the product path"), h("dl", { class: "kv" }, Object.entries(prod.statusCounts).map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])))) : null,
    h("p", { class: "small muted" }, "“Quote verification” (the cited span exists at its offsets) is reported separately from whether the quote supports the extracted meaning; the former is a substring check and is not proof of the latter."));
}

// ---- verification: differential / mutation / streaming ---------------------------------------------------
function verification(s) {
  const d = s.data;
  const rows = [];
  if (d.mutation) rows.push(["Differential harness sensitivity", `${Object.keys(d.mutation.mutants).length - d.mutation.survivors.length} of ${Object.keys(d.mutation.mutants).length} planted defect classes detected`, d.mutation.survivors.length ? "bad" : "ok"]);
  if (d.streamAgree) rows.push(["Batch = streaming under the lateness policy", `${d.streamAgree.results.length - d.streamAgree.regimesWithDisagreement} of ${d.streamAgree.results.length} regimes agree · ${d.streamAgree.results.reduce((a, r) => a + r.scenarios, 0)} scenario runs · ${d.streamAgree.results.reduce((a, r) => a + r.late, 0)} late events reported and excluded exactly`, d.streamAgree.regimesWithDisagreement ? "bad" : "ok"]);
  if (d.recovery) rows.push(["Hard kill + restart", `${d.recovery.runs.filter((r) => !r.problems.length).length} of ${d.recovery.runs.length} kill points recovered with no missing or duplicated alert; mid-stream redelivery → ${d.recovery.runs[0].duplicateRecordsForMidStreamRetry} duplicate records; post-expiry redelivery → ${d.recovery.runs[0].rowsDroppedByWatermarkAfterHeartbeat} rows counted as dropped by the watermark, 0 new alerts`, d.recovery.runs.some((r) => r.problems.length) ? "bad" : "ok"]);
  return table([{ label: "Check", render: (r) => r[0] }, { label: "Result", render: (r) => r[1] }, { label: "", render: (r) => chip(r[2] === "ok" ? "ok" : "failed", r[2] === "ok" ? "Passed" : "Failed") }], rows);
}

// ---- benchmarks ---------------------------------------------------------------------------------------------
function benchmarks(s) {
  const out = [];
  for (const [key, b] of Object.entries(s.data)) {
    if (!b) continue;
    const hw = b.hardware, jv = b.jvm;
    out.push(h("div", { class: "card", style: "box-shadow:none" },
      h("h3", {}, `${b.name} — ${fmt.n(b.datasetShape.events)} events`),
      h("p", { class: "small muted" }, `${hw.cpuModel} · ${hw.logicalCpus} logical CPUs · ${hw.ramGB} GB RAM · JVM heap ${jv.maxHeapMB} MB · ${jv.master} · Spark ${jv.sparkVersion} · ${hw.machinesUsed} machine · ${b.mode}. JVM→session ${b.coldStart.jvmToSessionReadySeconds.toFixed(1)} s (cold start), first full scan ${b.coldStart.firstFullScanSeconds.toFixed(1)} s.`),
      table([{ label: "Behaviour", render: (r) => r[0] }, { label: "n (warm)", num: true, render: (r) => r[1].warmSeconds.n }, { label: "Median (s)", num: true, render: (r) => r[1].warmSeconds.median?.toFixed(2) },
        { label: "Range (s)", num: true, render: (r) => `${r[1].warmSeconds.min?.toFixed(2)}–${r[1].warmSeconds.max?.toFixed(2)}` }, { label: "p95 (s)", num: true, render: (r) => (r[1].warmSeconds.p95 ? r[1].warmSeconds.p95.toFixed(2) : "n too small") },
        { label: "Events/s at median", num: true, render: (r) => fmt.n(Math.round(r[1].eventsPerSecondAtMedian)) }, { label: "Peak heap (MB)", num: true, render: (r) => fmt.n(Math.round(r[1].peakHeapMB.max)) },
        { label: "Failed", num: true, render: (r) => `${r[1].failed}/${r[1].attempted}` }], Object.entries(b.perBehaviour))));
  }
  return out.length ? h("div", { class: "stack" }, out) : empty("No benchmark results committed yet");
}

const RENDER = { audit, holdout, verification, benchmarks };

export async function evaluation() {
  const ev = await api("/api/evaluation");
  if (!ev.sections.length) return h("div", { class: "stack" }, h("div", { class: "page-head" }, h("h1", {}, "Evaluation")), empty("No archived evaluation results found", "See docs/evaluation.md."));
  return h("div", { class: "stack" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Evaluation"), h("p", {}, "Every number here was measured earlier and committed to the repository. Each block says on what data, whether that data was tuned against, and how to reproduce it."))),
    banner("info", "Read these as archived results", "Nothing on this page is computed live. Dataset kinds: regression = tuned against; frozen holdout = untouched until the single reported run; synthetic = generated."),
    ev.sections.map((s) => h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", {}, s.title), h("div", { class: "row" }, chip("archived"), s.dataKind ? chip(s.dataKind === "frozen-holdout" ? "verified" : "uncertain", s.dataLabel || s.dataKind) : null)),
      s.summary ? h("p", { class: "muted" }, s.summary) : null,
      s.missing?.length ? banner("warn", "Some result files are missing", s.missing.join(", ")) : null,
      (RENDER[s.type] || (() => h("pre", { class: "cmd" }, JSON.stringify(s.data, null, 2).slice(0, 3000))))(s),
      s.caveats?.length ? h("ul", { class: "plain small muted", style: "margin-top:8px" }, s.caveats.map((c) => h("li", {}, c))) : null,
      s.reproduce ? h("details", { style: "margin-top:8px" }, h("summary", {}, "How to reproduce"), h("pre", { class: "cmd" }, s.reproduce)) : null)));
}
