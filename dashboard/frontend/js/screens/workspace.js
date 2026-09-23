import { api, pollJob } from "../api.js";
import { banner, chip, clear, empty, flash, fmt, h, loading, toast } from "../ui.js";

// Report workspace: the report text beside what was extracted from it, with every value tied to a highlighted passage.

const STAGES = [["queued", "Queued"], ["extracting", "Extracting"], ["validating", "Validating"]];

function stepper(job) {
  const state = job?.state || "queued";
  const order = ["queued", "extracting", "validating"];
  const idx = order.indexOf(state);
  const end = { ready: ["okend", "Ready"], needs_review: ["warn", "Needs review"], rejected: ["bad", "Rejected"], failed: ["bad", "Failed"] }[state];
  return h("div", { class: "steps", role: "list", "aria-label": "Job progress" },
    STAGES.map(([k, label], i) => h("div", { class: `step ${end || i < idx ? "done" : i === idx ? "now" : ""}`, role: "listitem" }, label)),
    h("div", { class: `step ${end ? end[0] : ""}`, role: "listitem" }, end ? end[1] : "Result"));
}

// ---- new analysis form ---------------------------------------------------------------------
async function form({ ctx, navigate }) {
  const [samples, datasets] = await Promise.all([api("/api/samples"), api("/api/datasets")]);
  const ta = h("textarea", { id: "report-text", placeholder: "Paste a threat report, advisory, ticket or runbook…", "aria-label": "Report text" });
  const extractor = h("select", { id: "extractor" }, ctx.config.extractors.map((e) => h("option", { value: e.id, disabled: !e.available, selected: e.id === ctx.config.defaultExtractor }, `${e.label}${e.available ? "" : " — unavailable"}`)));
  const dataset = h("select", { id: "dataset" }, datasets.datasets.map((d) => h("option", { value: d.id }, `${d.name} (v${d.version})`)));
  const sampleSel = h("select", { "aria-label": "Load a sample report", onchange: async (e) => {
    if (!e.target.value) return;
    const s = await api(`/api/samples/${e.target.value}`);
    ta.value = s.text;
    hint.textContent = `Expected: ${({ compiled: "compiles to a rule", rejected: "rejected with reasons", needs_review: "needs review" })[s.expect]}. ${s.note}`;
  } }, h("option", { value: "" }, "Load a sample report…"), samples.samples.map((s) => h("option", { value: s.id }, `${s.title}`)));
  const hint = h("p", { class: "small muted", style: "min-height:1.4em" });
  try { const pre = sessionStorage.getItem("sf.prefill"); if (pre) { ta.value = pre; sessionStorage.removeItem("sf.prefill"); hint.textContent = "Loaded the report you were reviewing — edit it and analyze again."; } } catch { /* ignore */ }
  const status = h("div", {});
  const go = h("button", { class: "primary", onclick: async () => {
    const text = ta.value;
    if (text.trim().length < 20) { toast("Paste a report first (at least a sentence).", "bad"); return; }
    go.disabled = true;
    clear(status).append(stepper({ state: "queued" }));
    try {
      const rep = await api("/api/reports", { method: "POST", body: { text } });
      const a = await api(`/api/reports/${rep.id}/analyses`, { method: "POST", body: { extractor: extractor.value, datasetId: dataset.value } });
      await pollJob(a.jobId, (job) => clear(status).append(stepper(job)));
      navigate(`#/workspace/${a.analysisId}`);
    } catch (e) {
      go.disabled = false;
      clear(status).append(banner("bad", "Could not analyze the report", e.message));
    }
  } }, "Analyze report");
  return h("div", { class: "stack" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Report workspace"),
      h("p", {}, "Paste a report. Nothing is compiled until every number in the rule can be pointed to in the text — and the data you have can evaluate it."))),
    h("div", { class: "card" },
      h("div", { class: "row", style: "margin-bottom:10px" }, sampleSel, h("span", { class: "spacer" }), h("span", { class: "small muted" }, "Text is stored on this server; the prompted-LLM extractor is the only option that sends it elsewhere.")),
      ta, hint,
      h("div", { class: "row", style: "margin-top:10px" },
        h("div", { class: "field" }, h("label", { for: "extractor" }, "Extractor"), extractor),
        h("div", { class: "field" }, h("label", { for: "dataset" }, "Validate against dataset"), dataset),
        h("span", { class: "spacer" }), go),
      h("div", { style: "margin-top:10px" }, status)));
}

// ---- highlighting --------------------------------------------------------------------------
function collectSpans(result) {
  const rec = result.reconciliation || {};
  const spans = [];
  const spec = rec.spec || {};
  const cond = spec.conditions || {};
  const add = (ev, role, label, ref) => { if (ev && ev.end > ev.start) spans.push({ start: ev.start, end: ev.end, role, label, ref }); };
  (rec.reasons || []).forEach((r, i) => (r.evidence || []).forEach((e) => add(e, "problem", r.code, `reason-${i}`)));
  add(cond.count?.evidence, "count", "COUNT", "cond-count");
  add(cond.window?.evidence, "window", "WINDOW", "cond-window");
  const sig = ((rec.conditions || {}).signals || []).find((s) => s.behaviourId === rec.behaviourId);
  (sig?.cues || []).forEach((c) => add(c, "cue", "CUE", "cond-behaviour"));
  const prio = { problem: 0, count: 1, window: 2, cue: 3 };
  spans.sort((a, b) => a.start - b.start || prio[a.role] - prio[b.role]);
  const kept = [];
  for (const s of spans) { if (!kept.length || s.start >= kept[kept.length - 1].end) kept.push(s); }
  return kept;
}

function renderReport(text, spans, onPick) {
  const box = h("div", { class: "report", tabindex: 0, "aria-label": "Report text with highlighted evidence" });
  let pos = 0;
  spans.forEach((s, i) => {
    if (s.start > pos) box.append(text.slice(pos, s.start));
    const m = h("span", { class: "hl", dataset: { role: s.role, ref: s.ref, i: String(i) }, title: `${s.label}: ${s.role === "problem" ? "evidence for a problem" : "quoted evidence"}`, tabindex: 0,
      onclick: () => onPick(s.ref), onkeydown: (e) => { if (e.key === "Enter") onPick(s.ref); } }, text.slice(s.start, s.end), h("sup", {}, s.label));
    box.append(m);
    pos = s.end;
  });
  if (pos < text.length) box.append(text.slice(pos));
  return box;
}

// ---- result panel ---------------------------------------------------------------------------
function condCard(id, kind, value, sub, ev, verdict) {
  const card = h("div", { class: "cond", id, tabindex: 0 },
    h("div", { class: "row" }, h("span", { class: "k" }, kind), h("span", { class: "spacer" }), verdict),
    h("div", { class: "v" }, value), sub ? h("div", { class: "small muted" }, sub) : null,
    ev ? h("div", { class: "quote" }, `“${ev.quote}”`, h("div", { class: "faint small" }, `characters ${ev.start}–${ev.end} · quote-verified (it exists at those offsets)`)) : null);
  return card;
}

async function result({ ctx, params, navigate }) {
  let a = await api(`/api/analyses/${params[0]}`);
  const wrap = h("div", { class: "stack" });
  if (a.job && !["ready", "needs_review", "rejected", "failed"].includes(a.job.state)) {
    const status = h("div", {});
    const box = h("div", { class: "card" }, h("h2", {}, "Analyzing…"), status);
    clear(wrap).append(box);
    pollJob(a.job.id, (j) => clear(status).append(stepper(j))).then(() => navigate(`#/workspace/${params[0]}`));
    return wrap;
  }
  const res = a.result || {};
  const rec = res.reconciliation || {};
  const spec = rec.spec || {};
  const cond = spec.conditions || {};
  const beh = ctx.config.behaviours.find((b) => b.id === rec.behaviourId);
  const spans = a.status === "failed" ? [] : collectSpans(res);
  const cards = h("div", {});
  const pick = (ref) => { const el = document.getElementById(ref); if (el) flash(el); };

  // status banner + reasons
  let top;
  if (a.status === "ready") top = banner("ok", "Every condition is backed by the report and the data can evaluate it", "Nothing here is guessed. Review the highlighted passages, then check the validation.");
  else if (a.status === "needs_review") top = banner("warn", "Needs review: evidence is missing or two readings disagree", "Nothing was compiled. This is not a rejection — the report may be fine but incomplete.");
  else if (a.status === "rejected") top = banner("bad", res.rejectionKind === "data" ? "Rejected: this dataset cannot evaluate the rule" : "Rejected: the report cannot be turned into a supported rule", "No rule was compiled. The reasons below cite the passage that caused each one.");
  else top = banner("bad", "Analysis failed", res.error?.message || "See the job history.");
  const reasons = (rec.reasons || []).map((r, i) => h("div", { class: "cond", id: `reason-${i}` },
    h("div", { class: "row" }, chip(r.severity === "reject" ? "rejected" : "needs_review", r.code)),
    h("p", { style: "margin:6px 0 0" }, r.message),
    (r.evidence || []).slice(0, 3).map((e) => h("div", { class: "quote" }, `“${e.quote}”`, " ", h("button", { class: "quiet", onclick: () => { const el = document.querySelector(`.hl[data-ref="reason-${i}"]`); if (el) flash(el); } }, "show in report")))));

  // conditions
  if (rec.behaviourId) {
    cards.append(condCard("cond-behaviour", "Behaviour", beh?.name || rec.behaviourId, beh?.checks, null, rec.reasons?.some((r) => ["BEHAVIOUR_CONFLICT", "RECIPE_CONDITIONS_NOT_FOUND", "BEHAVIOUR_AMBIGUOUS"].includes(r.code)) ? chip("uncertain") : chip("verified", "Cues found in the text")));
    if (cond.count) cards.append(condCard("cond-count", "Count", `at least ${cond.count.value} — ${cond.count.semantics.replace("_", " ")}`, `Semantics are explicit: ${cond.count.semantics === "event_count" ? "counts events" : cond.count.semantics === "distinct_accounts" ? "counts distinct accounts, not attempts" : "counts distinct hosts"}. “${cond.count.form}” form.`, cond.count.evidence, chip("verified")));
    if (cond.window) cards.append(condCard("cond-window", "Time window", fmt.secs(cond.window.seconds), `read from the text: ${cond.window.amount} ${cond.window.unit}; closed interval, microsecond precision`, cond.window.evidence, chip("verified")));
    if (beh && beh.recipe === "PolicyCompare") cards.append(condCard("cond-policy", "Comparison", beh.checks, "No count or window: each successful login is compared with the account's policy record.", null, chip("verified")));
  }
  // model vs evidence
  const p = res.proposal;
  const claim = p ? h("div", { class: "card" }, h("h3", {}, "What the extractor claimed vs. what the passage backs"),
    h("dl", { class: "kv" },
      h("dt", {}, "Behaviour"), h("dd", {}, p.behaviourId || "abstained (no supported behaviour)"),
      h("dt", {}, "Threshold"), h("dd", {}, p.threshold ? JSON.stringify(p.threshold) : "—", cond.count ? h("span", { class: "muted" }, ` → passage says ${cond.count.value}`) : null),
      h("dt", {}, "Window"), h("dd", {}, p.timeWindow ? `${p.timeWindow.amount} ${p.timeWindow.unit}` : "—", cond.window ? h("span", { class: "muted" }, ` → passage says ${cond.window.amount} ${cond.window.unit}`) : null)),
    res.extractorMeta?.modelSignal ? h("p", { class: "small muted", style: "margin-top:8px" }, `Model signal (uncalibrated softmax): ${res.extractorMeta.modelSignal.behaviourSoftmax}. Never used to authorise compilation.`) : null) : null;

  const unc = (rec.uncertainties || []).length ? h("div", { class: "card" }, h("h3", {}, "What remains uncertain"), h("ul", { class: "plain small" }, rec.uncertainties.map((u) => h("li", {}, u)))) : null;

  const actions = h("div", { class: "row" },
    a.status === "ready" || a.status === "rejected" || a.status === "needs_review" ? h("a", { class: "btn", href: `#/validation/${a.id}` }, "Validation review →") : null,
    a.status === "ready" ? h("button", { class: "primary", onclick: async (e) => {
      e.target.disabled = true;
      try { const rv = await api(`/api/analyses/${a.id}/rule`, { method: "POST" }); toast("Rule version created from the stored evidence."); navigate(`#/validation/${a.id}`); }
      catch (err) { e.target.disabled = false; toast(err.message, "bad"); }
    } }, "Create rule from this evidence") : null,
    h("a", { class: "btn", href: "#/workspace" }, "New report"));

  return h("div", { class: "stack" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, a.report.title), h("p", {}, `Analyzed with ${res.extractorMeta?.extractor || a.extractor} · ${res.dataset ? `checked against ${res.dataset.name} v${res.dataset.version}` : "no dataset selected"}`)), chip(a.status)),
    top,
    h("div", { class: "grid split" },
      h("div", {}, h("div", { class: "legend", "aria-label": "Highlight key" },
        h("span", {}, h("i", { dataset: { role: "count" } }), "Count"), h("span", {}, h("i", { dataset: { role: "window" } }), "Time window"),
        h("span", {}, h("i", { dataset: { role: "cue" } }), "Behaviour cue"), h("span", {}, h("i", { dataset: { role: "problem" } }), "Problem")),
        renderReport(a.report.text, spans, pick)),
      h("div", { class: "stack" }, h("div", { class: "card" }, h("h2", {}, "Extracted conditions"), stepper(a.job || { state: a.status }), reasons.length ? h("div", { style: "margin:12px 0" }, reasons) : null, cards.childNodes.length ? cards : (reasons.length ? null : empty("Nothing was extracted", "The report did not contain a recognisable detection condition."))),
        claim, unc, actions)));
}

export async function workspace(env) {
  return env.params[0] ? result(env) : form(env);
}
