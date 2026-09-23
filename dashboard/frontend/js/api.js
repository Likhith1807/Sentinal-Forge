// Thin fetch wrapper. Errors carry the server's own explanation so screens can show it verbatim.
const TOKEN_KEY = "sf.token";

export function getToken() { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } }
export function setToken(t) { try { sessionStorage.setItem(TOKEN_KEY, t); } catch { /* private mode: token lives for this page only */ } }

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.detail || body?.error || `HTTP ${status}`);
    this.status = status; this.body = body || {};
  }
}

let onAuthNeeded = null;
export function setAuthHandler(fn) { onAuthNeeded = fn; }

export async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new ApiError(0, { error: "Cannot reach the server", detail: "The API did not respond. Is the service running?" });
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && onAuthNeeded) { onAuthNeeded(); }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

// Poll a job until it reaches a terminal state; `onUpdate` sees every state change (for the progress stepper).
export async function pollJob(jobId, onUpdate, { intervalMs = 400, timeoutMs = 300000 } = {}) {
  const terminal = new Set(["ready", "needs_review", "rejected", "completed", "failed"]);
  const start = Date.now();
  let last = "";
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.state !== last) { last = job.state; onUpdate?.(job); }
    if (terminal.has(job.state)) return job;
    if (Date.now() - start > timeoutMs) throw new ApiError(0, { error: "Timed out", detail: "The job is still running; check back from the Overview." });
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
