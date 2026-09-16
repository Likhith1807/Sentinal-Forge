const state = {
  reports: [],
  adversarial: [],
  behaviours: [],
  currentId: null,
  currentText: "",
  currentMeta: null,
  analysis: null,
  removedFields: new Set(),
  extractor: "transformer",
  overrideInjection: false,
  replayResults: null,
  auditLog: [],
};

const el = (tag, attrs = {}, children = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
};

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// ---------- boot ----------

async function boot() {
  const [reportsRes, behavioursRes, auditRes] = await Promise.all([
    api("/api/reports"),
    api("/api/behaviours"),
    api("/api/audit-log"),
  ]);
  state.reports = reportsRes.reports;
  state.adversarial = reportsRes.adversarialFixtures;
  state.behaviours = behavioursRes.behaviours;
  state.auditLog = auditRes.log;
  renderSidebar();
}

function behaviourShortName(id) {
  const b = state.behaviours.find((x) => x.id === id);
  return b ? b.shortName : id;
}

// ---------- sidebar ----------

function renderSidebar() {
  const container = document.getElementById("report-list");
  container.innerHTML = "";

  for (const b of state.behaviours) {
    container.appendChild(el("div", { class: "sidebar-section-title" }, b.shortName));
    const items = state.reports.filter((r) => r.behaviourId === b.id);
    for (const r of items) {
      container.appendChild(reportItem(r.id, [
        el("span", { class: "badge " + (r.split === "held-out" ? "held-out" : "train") }, r.split),
      ]));
    }
  }

  container.appendChild(el("div", { class: "sidebar-section-title" }, "Adversarial fixtures"));
  for (const r of state.adversarial) {
    container.appendChild(reportItem(r.id, [el("span", { class: "badge adversarial" }, "injection test")]));
  }
}

function reportItem(id, metaBadges) {
  const item = el("div", {
    class: "report-item" + (id === state.currentId ? " active" : ""),
    onclick: () => selectReport(id),
  }, [
    el("div", { class: "name" }, id),
    el("div", { class: "meta" }, metaBadges),
  ]);
  return item;
}

// ---------- selecting a report ----------

async function selectReport(id) {
  state.currentId = id;
  state.analysis = null;
  state.removedFields = new Set();
  state.overrideInjection = false;
  document.getElementById("empty-state").style.display = "none";
  const view = document.getElementById("report-view");
  view.style.display = "block";

  const data = await api(`/api/reports/${id}`);
  state.currentText = data.text;
  state.currentMeta = state.reports.find((r) => r.id === id) || state.adversarial.find((r) => r.id === id) || null;

  renderSidebar();
  renderReportView();
}

// ---------- main view ----------

function renderReportView() {
  const view = document.getElementById("report-view");
  view.innerHTML = "";

  view.appendChild(el("div", { class: "report-header" }, [
    el("h2", {}, state.currentId),
    el("div", { class: "sub" }, state.currentMeta
      ? (state.currentMeta.adversarial
          ? `Adversarial fixture — ${state.currentMeta.label}`
          : `${behaviourShortName(state.currentMeta.behaviourId)} · ${state.currentMeta.split} · ${state.currentMeta.kind}`)
      : ""),
  ]));

  // Plain string child becomes a text node (see el()), which the DOM
  // renders safely without any manual escaping — escaping here as well
  // would double-escape (an "&" in the report would show as "&amp;").
  view.appendChild(el("div", { class: "report-text", id: "report-text" }, state.currentText));

  view.appendChild(el("div", { class: "controls-row" }, [
    el("select", { id: "extractor-select", autocomplete: "off", onchange: (e) => { state.extractor = e.target.value; } }, [
      el("option", { value: "transformer", selected: state.extractor === "transformer" ? "selected" : null }, "Transformer (LLM) extractor"),
      el("option", { value: "classical", selected: state.extractor === "classical" ? "selected" : null }, "Classical (regex) extractor"),
    ]),
    el("button", { class: "primary", onclick: () => runAnalysis(state.overrideInjection) }, "Analyze report"),
  ]));

  const resultsContainer = el("div", { id: "results-container" });
  view.appendChild(resultsContainer);
  if (state.analysis) renderAnalysis(resultsContainer);

  view.appendChild(renderReplayPanel());
  view.appendChild(renderDecisionPanel());
  view.appendChild(renderAuditPanel());

  loadReplayResultsIfNeeded();
}

// ---------- analysis ----------

async function runAnalysis(overrideBlock) {
  // Read the <select>'s live DOM value rather than trusting state.extractor:
  // Chrome's form-state restoration can visually re-select an option after
  // a reload without firing a change event, leaving a separately-tracked
  // JS variable stale while the UI shows something else. Reading the DOM
  // directly is what the user actually sees, so it can't desync.
  const selectEl = document.getElementById("extractor-select");
  if (selectEl) state.extractor = selectEl.value;

  const body = {
    report_id: state.currentId,
    extractor: state.extractor,
    override_injection_block: overrideBlock,
    remove_fields: Array.from(state.removedFields),
  };
  const result = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  state.analysis = result;
  const container = document.getElementById("results-container");
  container.innerHTML = "";
  renderAnalysis(container);
}

function renderAnalysis(container) {
  const a = state.analysis;

  if (a.injectionGuard && a.injectionGuard.flagged) {
    // Not-blocked has two real causes, and the message must not conflate
    // them: classical extraction is structurally immune (no LLM call at
    // all, so nothing here to override), while transformer extraction
    // only proceeds because the analyst explicitly clicked past the block.
    let subMessage;
    if (a.blocked) subMessage = a.reason;
    else if (a.extractor === "classical") subMessage = "Classical extraction proceeded unaffected — it never sends report text to a model, so there is nothing here for an injection attempt to influence.";
    else subMessage = "Proceeding anyway — the analyst explicitly overrode the block.";

    container.appendChild(el("div", { class: "injection-warning" }, [
      el("strong", {}, "⚠ Prompt-injection guard flagged this report."),
      el("div", {}, subMessage),
      el("ul", {}, a.injectionGuard.matches.map((m) => el("li", {}, `"${m.text}"`))),
    ]));
  }

  if (a.blocked) {
    container.appendChild(el("button", { class: "primary", onclick: () => { state.overrideInjection = true; runAnalysis(true); } }, "Override and run LLM extractor anyway"));
    return;
  }

  const spec = a.spec;
  const panel = el("div", { class: "panel" });
  panel.appendChild(el("h3", {}, "Extracted specification"));
  panel.appendChild(el("div", {}, [
    el("span", { class: "status-pill " + a.stage3.status }, a.stage3.status),
    el("span", { html: `&nbsp;&nbsp;behaviourId: <code>${escapeHtml(String(spec.behaviourId))}</code>` }),
  ]));

  panel.appendChild(el("div", { style: "margin-top:10px" }, "Required log fields:"));
  panel.appendChild(fieldChipRow(spec.requiredFields, false));
  panel.appendChild(el("div", { style: "margin-top:8px" }, "Policy reference fields:"));
  panel.appendChild(fieldChipRow(spec.policyFields, true));

  if (a.stage3.notes && a.stage3.notes.length) {
    panel.appendChild(el("ul", { class: "notes-list" }, a.stage3.notes.map((n) => el("li", {}, n))));
  }
  container.appendChild(panel);

  if (a.confidence) {
    const c = a.confidence;
    const confPanel = el("div", { class: "panel" });
    confPanel.appendChild(el("h3", {}, "Confidence (cross-extractor agreement)"));
    confPanel.appendChild(el("div", {}, [
      el("span", { class: "status-pill " + (c.level === "high" ? "alert-good" : "insufficient_context") },
        c.level === "high" ? "high confidence" : "low confidence — recommend review"),
      el("span", { html: `&nbsp;&nbsp;agreement: <code>${c.agreement}</code>` }),
    ]));
    if (c.recommendReview) {
      confPanel.appendChild(el("div", { style: "margin-top:8px;font-size:12.5px;color:var(--muted)" }, [
        el("div", {}, `Classical extraction proposed: ${c.classicalFields.join(", ") || "(none)"}`),
        el("div", {}, `Transformer extraction proposed: ${c.transformerFields.join(", ") || "(none)"}`),
      ]));
    }
    confPanel.appendChild(el("div", { class: "remove-hint" }, c.note));
    container.appendChild(confPanel);
  }

  // Evidence panel
  const evidencePanel = el("div", { class: "panel" });
  evidencePanel.appendChild(el("h3", {}, "Evidence (source-text provenance)"));
  const provEntries = Object.entries(spec.provenance || {});
  if (!provEntries.length) {
    evidencePanel.appendChild(el("div", { style: "color:var(--muted);font-size:12.5px" }, "No provenance entries."));
  }
  for (const [field, prov] of provEntries) {
    const verified = prov.verified !== false && (prov.charStart !== undefined);
    evidencePanel.appendChild(el("div", { class: "evidence-row" }, [
      el("span", { class: "field-name" }, field),
      verified
        ? el("span", { class: "quote" }, `"${prov.text}"`)
        : el("span", { class: "quote", style: "color:var(--bad)" }, "unverified claimed quote — not shown in source"),
    ]));
  }
  container.appendChild(evidencePanel);

  applyEvidenceHighlights(provEntries);

  // "remove a field" live-degradation panel
  const degradePanel = el("div", { class: "panel" });
  degradePanel.appendChild(el("h3", {}, "Simulate a field becoming unavailable"));
  const allFields = [...spec.requiredFields, ...spec.policyFields];
  const chipRow = el("div", { class: "field-chip-row" });
  for (const f of allFields) {
    const checked = !state.removedFields.has(f);
    const input = el("input", {
      type: "checkbox",
      checked: checked ? "checked" : null,
      onchange: (e) => {
        if (e.target.checked) state.removedFields.delete(f); else state.removedFields.add(f);
        runAnalysis(state.overrideInjection);
      },
    });
    chipRow.appendChild(el("label", { class: "field-chip" + (f.startsWith("policy.") ? " policy" : "") }, [input, f]));
  }
  degradePanel.appendChild(chipRow);
  degradePanel.appendChild(el("div", { class: "remove-hint" }, "Uncheck a field to simulate it disappearing from the schema, then watch Stage 3's verdict update live."));
  container.appendChild(degradePanel);
}

function fieldChipRow(fields, isPolicy) {
  return el("div", { class: "field-chip-row" }, (fields && fields.length ? fields : ["(none)"]).map((f) =>
    el("span", { class: "field-chip" + (isPolicy ? " policy" : "") }, f)));
}

function applyEvidenceHighlights(provEntries) {
  const textEl = document.getElementById("report-text");
  const raw = state.currentText;
  const spans = provEntries
    .map(([, p]) => p)
    .filter((p) => p.charStart !== undefined && p.charEnd !== undefined)
    .sort((a, b) => a.charStart - b.charStart);

  let html = "";
  let cursor = 0;
  for (const s of spans) {
    if (s.charStart < cursor) continue; // skip overlaps, keep simple
    html += escapeHtml(raw.slice(cursor, s.charStart));
    html += `<mark>${escapeHtml(raw.slice(s.charStart, s.charEnd))}</mark>`;
    cursor = s.charEnd;
  }
  html += escapeHtml(raw.slice(cursor));
  textEl.innerHTML = html;
}

// ---------- replay results panel ----------

async function loadReplayResultsIfNeeded() {
  if (!state.replayResults) {
    state.replayResults = await api("/api/replay-results");
  }
  renderReplaySection();
}

function renderReplayPanel() {
  const panel = el("div", { class: "panel", id: "replay-panel" });
  panel.appendChild(el("h3", {}, "Real replay results (SENTINEL Forge full, Phase 4/5)"));
  panel.appendChild(el("div", { id: "replay-content", style: "color:var(--muted);font-size:12.5px" }, "Loading..."));
  return panel;
}

function renderReplaySection() {
  const content = document.getElementById("replay-content");
  if (!content) return;
  const behaviourId = state.currentMeta && !state.currentMeta.adversarial ? state.currentMeta.behaviourId : null;
  const rows = (state.replayResults.sentinelForgeFull || []).filter((r) => !behaviourId || r.behaviourId === behaviourId);
  if (!rows.length) {
    content.textContent = "No precomputed replay scenarios for this item.";
    return;
  }
  const table = el("table", {}, [
    el("thead", {}, el("tr", {}, [el("th", {}, "Scenario"), el("th", {}, "Expected"), el("th", {}, "Actual"), el("th", {}, "Result")])),
    el("tbody", {}, rows.map((r) => el("tr", {}, [
      el("td", { class: "mono" }, r.scenarioId),
      el("td", { class: "mono" }, r.expected),
      el("td", { class: "mono" }, r.actual),
      el("td", {}, el("span", { class: "status-pill " + (r.pass ? "alert-good" : "alert-bad") }, r.pass ? "PASS" : "FAIL")),
    ]))),
  ]);
  content.innerHTML = "";
  content.appendChild(table);
}

// ---------- decision + audit panels ----------

function renderDecisionPanel() {
  const panel = el("div", { class: "panel" });
  panel.appendChild(el("h3", {}, "Analyst decision"));
  const note = el("textarea", { id: "decision-note", placeholder: "Optional note (e.g. why refined, what evidence confirmed the approval)..." });
  panel.appendChild(note);
  panel.appendChild(el("div", { class: "controls-row", style: "margin-top:10px" }, [
    el("button", { class: "approve", onclick: () => submitDecision("approve") }, "Approve for production"),
    el("button", { class: "refine", onclick: () => submitDecision("refine") }, "Send back for refinement"),
  ]));
  return panel;
}

async function submitDecision(decision) {
  const note = document.getElementById("decision-note").value;
  const spec = state.analysis && !state.analysis.blocked ? state.analysis.spec : null;
  const result = await api("/api/decisions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ report_id: state.currentId, decision, note, spec }),
  });
  state.auditLog = result.log;
  renderReportView();
}

function renderDiff(diff) {
  if (!diff) return null;
  const parts = [];
  if (diff.behaviourIdChanged) parts.push("behaviourId changed");
  if (diff.fieldsAdded.length) parts.push(`+${diff.fieldsAdded.join(", +")}`);
  if (diff.fieldsRemoved.length) parts.push(`-${diff.fieldsRemoved.join(", -")}`);
  if (diff.thresholdChanged) parts.push(`threshold: ${JSON.stringify(diff.previousThreshold)} -> ${JSON.stringify(diff.currentThreshold)}`);
  if (diff.timeWindowChanged) parts.push(`timeWindow: ${JSON.stringify(diff.previousTimeWindow)} -> ${JSON.stringify(diff.currentTimeWindow)}`);
  if (!parts.length) return el("div", { style: "color:var(--muted);font-size:11.5px;margin-top:2px" }, "No change from previous version.");
  return el("div", { style: "color:var(--accent);font-size:11.5px;margin-top:2px;font-family:var(--mono)" }, "diff vs. previous: " + parts.join("  |  "));
}

function renderAuditPanel() {
  const panel = el("div", { class: "panel" });
  panel.appendChild(el("h3", {}, "Audit log"));
  if (!state.auditLog.length) {
    panel.appendChild(el("div", { style: "color:var(--muted);font-size:12.5px" }, "No decisions recorded yet."));
    return panel;
  }
  for (const entry of [...state.auditLog].reverse()) {
    panel.appendChild(el("div", { class: "audit-entry" }, [
      el("div", {}, [
        el("span", { class: "status-pill " + (entry.decision === "approve" ? "alert-good" : "insufficient_context") }, entry.decision),
        el("span", { html: `&nbsp;&nbsp;${escapeHtml(entry.reportId)}` }),
        entry.note ? el("div", { style: "color:var(--muted);margin-top:2px" }, entry.note) : null,
        renderDiff(entry.diffFromPreviousVersion),
      ]),
      el("div", { class: "ts" }, new Date(entry.timestamp).toLocaleString()),
    ]));
  }
  return panel;
}

boot();
