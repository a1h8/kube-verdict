// Minimal KubeVerdict API client for the Decision Journey view.
//
// No EventSource: the workflow emits only a handful of cumulative events
// (edge_log / reasoning_history grow in the server-side state), so we just poll
// GET /sessions/{id}/state. Each poll is a full snapshot — no event ordering,
// replay or missed-event handling. The bearer token (when set) rides as a
// normal Authorization header, which EventSource could not do.

const BASE = "/api/v1";

const TERMINAL = new Set(["AWAITING_REVIEW", "COMPLETED", "FAILED"]);

export function getToken() {
  return localStorage.getItem("kv_token") || "";
}

export function setToken(t) {
  if (t) localStorage.setItem("kv_token", t);
  else localStorage.removeItem("kv_token");
}

function headers() {
  const h = { "Content-Type": "application/json" };
  const t = getToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

async function req(path, opts = {}) {
  const r = await fetch(`${BASE}${path}`, { headers: headers(), ...opts });
  if (r.status === 401) throw new Error("401 — bearer token required or invalid");
  if (!r.ok) throw new Error(`${opts.method || "GET"} ${path} → ${r.status}`);
  return r.status === 204 ? null : r.json();
}

export const createSession = () => req("/sessions", { method: "POST" });

export const runSession = (id, body) =>
  req(`/sessions/${id}/run`, { method: "POST", body: JSON.stringify(body) });

export const getState = (id) => req(`/sessions/${id}/state`);

// Poll until the workflow reaches a terminal state, calling onTick with every
// snapshot so the UI can render the journey as it grows.
export async function pollState(id, { onTick, interval = 1500, timeout = 180000 } = {}) {
  const deadline = Date.now() + timeout;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const s = await getState(id);
    onTick?.(s);
    if (TERMINAL.has(s.status)) return s;
    if (Date.now() > deadline) throw new Error("poll timeout");
    await new Promise((r) => setTimeout(r, interval));
  }
}

// Convenience: create → run → poll to completion.
export async function investigate(body, { onTick } = {}) {
  const { session_id } = await createSession();
  await runSession(session_id, body);
  const final = await pollState(session_id, { onTick });
  return { session_id, state: final };
}
