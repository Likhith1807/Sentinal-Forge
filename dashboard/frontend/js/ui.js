// Small DOM helpers. Everything user- or server-supplied goes in as text nodes (never innerHTML).
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") throw new Error("html attribute is not allowed");
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

// ---- state chips: text label + glyph + colour, so colour is never the only signal
const CHIPS = {
  // jobs / analyses / runs
  queued: ["Queued", "", "…"], extracting: ["Extracting", "info", "◔"], validating: ["Validating", "info", "◔"], running: ["Running", "info", "◔"],
  ready: ["Ready", "ok", "✓"], completed: ["Completed", "ok", "✓"], needs_review: ["Needs review", "warn", "!"], rejected: ["Rejected", "bad", "✕"], failed: ["Failed", "bad", "✕"],
  // rules
  draft: ["Draft", "info", "○"], approved: ["Approved", "ok", "✓"], paused: ["Paused", "bad", "Ⅱ"], superseded: ["Superseded", "", "↷"], retired: ["Retired", "", "–"],
  // alerts
  alert: ["Alert", "bad", "▲"], insufficient_context: ["Insufficient context", "warn", "?"], no_alert: ["No alert", "ok", "✓"],
  // provenance
  fresh: ["Fresh run", "ok", "●"], demonstration: ["Demonstration data", "info", "◇"], archived: ["Archived result", "", "▤"], stale: ["Stale: data changed since", "warn", "!"],
  // verification
  verified: ["Quote verified", "ok", "✓"], uncertain: ["Uncertain", "warn", "!"], violation: ["Violation", "bad", "✕"], supported: ["Supported", "ok", "✓"],
  missing: ["Missing", "bad", "✕"], wrong_type: ["Wrong type", "bad", "✕"], simulated_missing: ["Removed", "bad", "✕"], unreliable: ["Unreliable", "warn", "!"], ok: ["OK", "ok", "✓"],
};
export function chip(state, label) {
  const [text, tone, glyph] = CHIPS[state] || [state, "", "•"];
  return h("span", { class: `chip ${tone}` }, h("span", { class: "g", "aria-hidden": "true" }, glyph), label || text);
}

export function banner(tone, title, body, actions) {
  const glyph = { ok: "✓", warn: "!", bad: "✕", info: "i" }[tone] || "i";
  return h("div", { class: `banner ${tone}`, role: tone === "bad" ? "alert" : "status" },
    h("div", { class: "glyph", "aria-hidden": "true" }, glyph),
    h("div", { style: "flex:1" }, title ? h("p", {}, h("strong", {}, title)) : null, body ? (typeof body === "string" ? h("p", {}, body) : body) : null), actions || null);
}

export function loading(text = "Loading…") { return h("div", { class: "state", role: "status" }, h("span", { class: "spin" }), text); }
export function empty(title, hint, action) { return h("div", { class: "state" }, h("div", { class: "big" }, title), hint ? h("div", {}, hint) : null, action ? h("div", { style: "margin-top:12px" }, action) : null); }
export function errorState(err, retry) {
  return h("div", { class: "state" }, h("div", { class: "big", style: "color:var(--bad)" }, "Something went wrong"),
    h("div", {}, err?.message || String(err)), retry ? h("div", { style: "margin-top:12px" }, h("button", { onclick: retry }, "Try again")) : null);
}

export function table(cols, rows, { onRow, selected, emptyText = "Nothing to show." } = {}) {
  if (!rows.length) return empty(emptyText);
  const thead = h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.num ? "num" : "" }, c.label))));
  const tbody = h("tbody", {}, rows.map((r) => {
    const tr = h("tr", { class: onRow ? "click" : "", tabindex: onRow ? 0 : undefined, "aria-selected": selected && selected(r) ? "true" : undefined },
      cols.map((c) => h("td", { class: `${c.num ? "num" : ""} ${c.cls || ""}` }, c.render(r))));
    if (onRow) {
      tr.addEventListener("click", () => onRow(r));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onRow(r); } });
    }
    return tr;
  }));
  return h("div", { class: "table-wrap" }, h("table", {}, thead, tbody));
}

export function toast(msg, tone = "") {
  const t = h("div", { class: `toast ${tone}` }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), tone === "bad" ? 8000 : 3800);
}

export const fmt = {
  n: (x) => (x === null || x === undefined ? "–" : Number(x).toLocaleString()),
  time: (s) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "–"),
  secs: (s) => {
    if (s === null || s === undefined) return "–";
    s = Number(s);
    if (s % 3600 === 0 && s >= 3600) return `${s / 3600} h (${s} s)`;
    if (s % 60 === 0 && s >= 60) return `${s / 60} min (${s} s)`;
    return `${s} s`;
  },
  short: (id) => (id ? String(id).slice(0, 8) : "–"),
  pct: (x) => (x === null || x === undefined ? "–" : `${(x * 100).toFixed(1)}%`),
};

export function summarizeRule(compiled) {
  if (!compiled) return "";
  const n = compiled.countThreshold ?? compiled.distinctThreshold;
  const bits = [];
  if (n !== null && n !== undefined) bits.push(`≥ ${n} ${({ event_count: "events", distinct_accounts: "distinct accounts", distinct_hosts: "distinct hosts" })[compiled.countSemantics] || ""}`);
  if (compiled.timeWindowSeconds) bits.push(`within ${fmt.secs(compiled.timeWindowSeconds)}`);
  return bits.join(" ") || "compares each event with policy";
}

export function flash(el) { el.classList.add("flash"); el.scrollIntoView({ block: "center", behavior: "smooth" }); setTimeout(() => el.classList.remove("flash"), 1600); }
