import { useState } from "react";
import { investigate, loadSample, getToken, setToken } from "./api.js";

// ── theme ────────────────────────────────────────────────────────────────────
const C = {
  bg: "#020617", panel: "#0b1220", border: "#1e293b",
  text: "#e2e8f0", sub: "#94a3b8", dim: "#475569",
};
const VERDICT = {
  AUTO:         { bg: "#052e1a", fg: "#34d399", label: "AUTO" },
  HUMAN_REVIEW: { bg: "#3a2e05", fg: "#fbbf24", label: "HUMAN REVIEW" },
  NO_GO:        { bg: "#3a0a0a", fg: "#f87171", label: "NO-GO" },
};
const EDGE = {
  retry:     { icon: "🔄", fg: "#fbbf24" },
  next_path: { icon: "➡️", fg: "#60a5fa" },
  review:    { icon: "👤", fg: "#34d399" },
  auto:      { icon: "⚙️", fg: "#34d399" },
  no_go:     { icon: "⛔", fg: "#f87171" },
};
const RISK = { LOW: "#34d399", MEDIUM: "#fbbf24", HIGH: "#fb923c", CRITICAL: "#f87171" };

const box = { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 16 };

// gate score the policy node actually recorded in edge_log (not recomputed)
function gateScore(state) {
  const log = state?.edge_log || [];
  for (let i = log.length - 1; i >= 0; i--) {
    if (log[i].router === "policy") {
      const s = log[i].snapshot?.score;
      return s == null ? null : Number(s);
    }
  }
  return null;
}

function VerdictBanner({ state }) {
  const v = VERDICT[state.verdict] || { bg: C.panel, fg: C.sub, label: state.verdict || "—" };
  const score = gateScore(state);
  const risk = state.blast_radius?.risk;
  return (
    <div style={{ ...box, background: v.bg, borderColor: v.fg }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
        <span style={{ fontSize: 22, fontWeight: 800, color: v.fg }}>{v.label}</span>
        <span style={{ color: C.sub }}>
          confidence <b style={{ color: C.text }}>{state.confidence || "—"}</b>
          {score != null && <> ({score.toFixed(2)})</>}
        </span>
        {risk && (
          <span style={{ color: C.sub }}>
            blast radius <b style={{ color: RISK[risk] || C.text }}>{risk}</b>
          </span>
        )}
      </div>
      {(state.verdict_reasons || []).length > 0 && (
        <ul style={{ margin: "10px 0 0 18px", color: C.sub, fontSize: 14 }}>
          {state.verdict_reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}

function Timeline({ state }) {
  const log = state.edge_log || [];
  if (!log.length) return null;
  return (
    <div style={box}>
      <h3 style={{ color: C.text, marginBottom: 12 }}>Decision timeline — {log.length} step(s)</h3>
      {log.map((e, i) => {
        const meta = EDGE[e.edge_taken] || { icon: "•", fg: C.sub };
        const snap = e.snapshot || {};
        return (
          <div key={i} style={{ borderLeft: `2px solid ${meta.fg}`, paddingLeft: 12, marginBottom: 12 }}>
            <div style={{ color: C.text, fontSize: 14 }}>
              {meta.icon} <code style={{ color: C.sub }}>{e.router}</code> →{" "}
              <b style={{ color: meta.fg }}>{e.edge_taken}</b>
            </div>
            {e.reason && <div style={{ color: C.sub, fontSize: 13, marginTop: 2 }}>{e.reason}</div>}
            {(snap.confidence || snap.score != null) && (
              <div style={{ color: C.dim, fontSize: 12, marginTop: 2 }}>
                {snap.confidence && <>conf={snap.confidence} </>}
                {snap.score != null && <>· score={Number(snap.score).toFixed(2)} </>}
                {snap.retry_count != null && <>· retry {snap.retry_count}/{snap.max_retries ?? "?"}</>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Paths({ state }) {
  const eliminated = state.reasoning_history || [];
  const chosen = state.current_hypothesis;
  if (!eliminated.length && !chosen) return null;
  return (
    <div style={box}>
      <h3 style={{ color: C.text, marginBottom: 12 }}>
        Hypothesis paths — {eliminated.length + (chosen ? 1 : 0)} explored
      </h3>
      {eliminated.map((p, i) => (
        <div key={i} style={{ color: C.sub, fontSize: 14, marginBottom: 8 }}>
          <span style={{ color: "#f87171" }}>✕</span> <b>Path {p.step ?? i + 1}</b>{" "}
          {p.hypothesis || "—"}{" "}
          <span style={{ color: C.dim }}>
            ({(p.confidence || "?")}{p.retry_count != null && `, ${p.retry_count} retr.`}) — eliminated
          </span>
          {p.summary && <div style={{ color: C.dim, fontSize: 12, marginLeft: 18 }}>{p.summary}</div>}
        </div>
      ))}
      {chosen && (
        <div style={{ color: C.text, fontSize: 14 }}>
          <span style={{ color: "#34d399" }}>✓</span> <b>Chosen</b> {chosen}{" "}
          <span style={{ color: C.dim }}>({state.confidence || "?"}) — selected</span>
        </div>
      )}
    </div>
  );
}

function RootCause({ state }) {
  const rc = state.report?.root_cause;
  const rem = state.report?.remediation || state.suggestions || [];
  if (!rc && !rem.length) return null;
  return (
    <div style={box}>
      <h3 style={{ color: C.text, marginBottom: 8 }}>Root cause</h3>
      <p style={{ color: C.sub, fontSize: 14, marginBottom: rem.length ? 12 : 0 }}>{rc || "—"}</p>
      {rem.map((cmd, i) => (
        <pre key={i} style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 6,
          padding: "8px 12px", color: "#7dd3fc", fontSize: 12.5, overflowX: "auto", marginBottom: 6 }}>
          {cmd}
        </pre>
      ))}
    </div>
  );
}

export default function DecisionJourney() {
  const [query, setQuery] = useState("pods crashlooping in production");
  const [namespace, setNamespace] = useState("");
  const [token, setTok] = useState(getToken());
  const [state, setState] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | running | done | error
  const [error, setError] = useState("");

  async function go(action) {
    setToken(token.trim());
    setError(""); setState(null); setStatus("running");
    try {
      const { state: final } = await action();
      setState(final);
      setStatus("done");
    } catch (e) {
      setError(String(e.message || e));
      setStatus("error");
    }
  }

  function run() {
    const body = { query };
    if (namespace.trim()) body.namespaces = [namespace.trim()];
    return go(() => investigate(body, { onTick: setState }));
  }

  const sample = () => go(() => loadSample({ onTick: setState }));

  const inputStyle = { background: C.bg, border: `1px solid ${C.border}`, color: C.text,
    borderRadius: 6, padding: "8px 10px", fontSize: 14 };

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text,
      fontFamily: "system-ui, sans-serif", padding: "24px 20px" }}>
      <div style={{ maxWidth: 860, margin: "0 auto" }}>
        <a href="#/" style={{ color: C.sub, fontSize: 14, textDecoration: "none" }}>← Home</a>
        <h1 style={{ fontSize: 26, fontWeight: 800, margin: "8px 0 4px" }}>Decision Journey</h1>
        <p style={{ color: C.sub, fontSize: 14, marginBottom: 20 }}>
          Live evidence-first RCA via the API — how it reasoned (paths explored / eliminated) and
          why it reached its verdict. Polls <code>GET /sessions/&#123;id&#125;/state</code>.
        </p>

        <div style={{ ...box, display: "grid", gap: 10 }}>
          <input style={inputStyle} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Query" />
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <input style={{ ...inputStyle, flex: 1, minWidth: 160 }} value={namespace}
              onChange={(e) => setNamespace(e.target.value)} placeholder="Namespace (optional)" />
            <input style={{ ...inputStyle, flex: 1, minWidth: 160 }} value={token} type="password"
              onChange={(e) => setTok(e.target.value)} placeholder="Bearer token (if API auth on)" />
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button onClick={run} disabled={status === "running"}
              style={{ background: status === "running" ? C.dim : "#2563eb", color: "#fff", border: "none",
                borderRadius: 6, padding: "10px 16px", fontSize: 15, fontWeight: 600,
                cursor: status === "running" ? "default" : "pointer" }}>
              {status === "running" ? "Investigating…" : "▶ Investigate"}
            </button>
            <button onClick={sample} disabled={status === "running"}
              title="Load a recorded sample — no cluster or Ollama needed"
              style={{ background: "transparent", color: C.sub, border: `1px solid ${C.border}`,
                borderRadius: 6, padding: "10px 16px", fontSize: 15, fontWeight: 600,
                cursor: status === "running" ? "default" : "pointer" }}>
              Load sample
            </button>
          </div>
        </div>

        {status === "running" && !state && <p style={{ color: C.sub }}>Starting…</p>}
        {error && (
          <div style={{ ...box, background: "#3a0a0a", borderColor: "#f87171", color: "#fecaca" }}>
            {error}
          </div>
        )}

        {state && (
          <>
            <div style={{ color: C.dim, fontSize: 13, marginBottom: 12 }}>
              status: <b style={{ color: C.sub }}>{state.status}</b>
              {status === "running" && " · polling…"}
            </div>
            {state.verdict && <VerdictBanner state={state} />}
            <Paths state={state} />
            <Timeline state={state} />
            <RootCause state={state} />
          </>
        )}
      </div>
    </div>
  );
}
