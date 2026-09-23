import { api, getToken, setAuthHandler, setToken } from "./api.js";
import { clear, errorState, h, loading } from "./ui.js";
import { overview } from "./screens/overview.js";
import { workspace } from "./screens/workspace.js";
import { validation } from "./screens/validation.js";
import { investigation } from "./screens/investigation.js";
import { evaluation } from "./screens/evaluation.js";

const view = document.getElementById("view");
const ctx = { config: null };

const ROUTES = [
  [/^#?\/?$/, "overview", overview],
  [/^#\/workspace(?:\/([\w.-]+))?$/, "workspace", workspace],
  [/^#\/validation(?:\/([\w.-]+))?$/, "validation", validation],
  [/^#\/investigation(?:\/([\w.-]+))?(?:\/alert\/([\w.-]+))?$/, "investigation", investigation],
  [/^#\/evaluation$/, "evaluation", evaluation],
];

let generation = 0;
async function route() {
  const hash = location.hash || "#/";
  const gen = ++generation;
  for (const [re, name, screen] of ROUTES) {
    const m = hash.match(re);
    if (!m) continue;
    document.querySelectorAll("#nav a").forEach((a) => { if (a.dataset.route === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
    clear(view).append(loading());
    try {
      const node = await screen({ ctx, params: m.slice(1), isCurrent: () => gen === generation, navigate: (h2) => { location.hash = h2; } });
      if (gen !== generation) return;             // the user navigated away while this screen was loading
      clear(view).append(node);
    } catch (e) {
      if (gen === generation) clear(view).append(errorState(e, route));
    }
    document.title = `${name[0].toUpperCase()}${name.slice(1)} · SENTINEL Forge`;
    return;
  }
  clear(view).append(errorState(new Error("Unknown page"), () => { location.hash = "#/"; }));
}

function askToken() {
  const dlg = h("div", { class: "card", style: "max-width:420px;margin:60px auto" },
    h("h2", {}, "Sign in"),
    h("p", { class: "muted" }, "This instance requires an access token. Ask the administrator, or read the token printed when the server started."),
    h("div", { class: "field" }, h("label", { for: "tok" }, "Access token"), h("input", { id: "tok", type: "password", autocomplete: "off" })),
    h("div", { style: "margin-top:12px" }, h("button", { class: "primary", onclick: () => { setToken(dlg.querySelector("#tok").value.trim()); boot(); } }, "Continue")));
  clear(view).append(dlg);
}
setAuthHandler(askToken);

async function boot() {
  try {
    ctx.config = await api("/api/config");
  } catch (e) {
    if (e.status === 401 && !getToken()) { askToken(); return; }
    if (e.status === 401) { askToken(); return; }
    clear(view).append(errorState(e, boot));
    return;
  }
  const who = document.getElementById("whoami");
  clear(who).append(
    h("span", { class: "chip" }, ctx.config.auth === "token" ? "Token auth" : "Local demo mode"),
    h("span", { class: "chip" }, `Engine: ${ctx.config.engine.selected === "spark" ? "Spark" : "Python reference"}`),
    h("span", {}, `${ctx.config.user.name} (${ctx.config.user.role})`));
  if (!window.__sfRouted) { window.__sfRouted = true; window.addEventListener("hashchange", route); }
  route();
}
boot();
