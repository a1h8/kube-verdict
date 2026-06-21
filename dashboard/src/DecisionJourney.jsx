import { useEffect, useState } from "react";
import { investigate, loadSample, reviewSession, getToken, setToken, getState } from "./api.js";

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
const SEV = { critical: "#f87171", warning: "#fbbf24", info: "#60a5fa" };

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

// The anchor-by-render wedge, made visible: the EXPECTED state is reconstructed
// by rendering the chart with `helm template`, then diffed against the live
// cluster. Each row shows declared → observed per field, so the verdict rests on
// concrete render-vs-live evidence rather than an LLM guess.
function EvidencePanel({ state }) {
  const rows = state.drift_evidence || [];
  if (!rows.length) return null;
  const crit = rows.reduce(
    (n, r) => n + (r.diffs || []).filter((d) => d.severity === "critical").length, 0);
  return (
    <div style={box}>
      <h3 style={{ color: C.text, marginBottom: 2 }}>
        Evidence — expected state (helm template) vs live
      </h3>
      <p style={{ color: C.dim, fontSize: 12, marginBottom: 12 }}>
        Rendered intent → live diff · {rows.length} resource(s)
        {crit > 0 && <> · <span style={{ color: SEV.critical }}>{crit} critical</span></>}
      </p>
      {rows.map((r, i) => (
        <div key={i} style={{ marginBottom: i < rows.length - 1 ? 14 : 0 }}>
          <div style={{ color: C.text, fontSize: 13.5, marginBottom: 6 }}>
            <code style={{ color: C.sub }}>{r.kind}</code>{" "}
            {r.namespace && <span style={{ color: C.dim }}>{r.namespace}/</span>}
            <b>{r.name}</b>
          </div>
          {(r.diffs || []).map((d, j) => {
            const fg = SEV[d.severity] || C.sub;
            return (
              <div key={j} style={{ display: "flex", alignItems: "baseline", gap: 8,
                flexWrap: "wrap", borderLeft: `2px solid ${fg}`, paddingLeft: 10,
                marginBottom: 5, fontSize: 13 }}>
                <code style={{ color: C.sub, minWidth: 220 }}>{d.field_path}</code>
                <span style={{ color: C.dim }}>declared</span>
                <b style={{ color: C.text }}>{String(d.declared)}</b>
                <span style={{ color: fg }}>→ live</span>
                <b style={{ color: fg }}>{String(d.observed)}</b>
                <span style={{ color: fg, fontSize: 11, textTransform: "uppercase",
                  letterSpacing: 0.5 }}>{d.severity}</span>
              </div>
            );
          })}
        </div>
      ))}
      <div style={{ color: C.dim, fontSize: 11, marginTop: 12 }}>
        source: rendered intent → live diff <code style={{ color: C.sub }}>[render-vs-live]</code>
      </div>
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

// B9: per-collector fallback overlay — green OK / red FALLBACK badge with the
// collector error surfaced as a tooltip (from ingestion_stats[*].fallback/error).
function FallbackStatus({ state }) {
  const stats = state.ingestion_stats || {};
  const rows = Object.entries(stats).filter(([, v]) => v && typeof v === "object");
  if (!rows.length) return null;
  return (
    <div style={box}>
      <h3 style={{ color: C.text, marginBottom: 10 }}>Collector status — {rows.length}</h3>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {rows.map(([name, v]) => {
          const fb = v.fallback === true;
          const fg = fb ? "#f87171" : "#34d399";
          return (
            <span
              key={name}
              title={v.error || (fb ? "fallback active" : "ok")}
              style={{
                border: `1px solid ${fg}`, color: fg, borderRadius: 6,
                padding: "3px 9px", fontSize: 12, cursor: v.error ? "help" : "default",
              }}
            >
              {fb ? "● " : "○ "}{name}: {fb ? "FALLBACK" : "OK"}
            </span>
          );
        })}
      </div>
    </div>
  );
}

// B9: beam-search tree — active path blue, archived branches gray, edges labelled
// with confidence, node size ∝ retries, eliminated leaves ✕ + reason on hover.
function BeamTree({ state }) {
  const eliminated = state.reasoning_history || [];
  const chosen = state.current_hypothesis;
  const nodes = eliminated.map((p, i) => ({
    label: p.hypothesis || `Path ${p.step ?? i + 1}`,
    conf: p.confidence || "?",
    retries: p.retry_count ?? 0,
    reason: p.summary || "confidence did not improve — branch archived",
    status: "archived",
  }));
  if (chosen) {
    nodes.push({
      label: chosen,
      conf: state.confidence || "?",
      retries: 0,
      reason: "selected — highest evidence-weighted confidence",
      status: "active",
    });
  }
  if (!nodes.length) return null;

  const W = 820, rootY = 40, leafBase = 172;
  const n = nodes.length;
  const xs = nodes.map((_, i) => (W * (i + 1)) / (n + 1));
  const rootX = W / 2;
  const STATUS = {
    archived: { fill: "#1e293b", stroke: "#475569", fg: "#94a3b8", mark: "✕" },
    active:   { fill: "#0b1f3a", stroke: "#60a5fa", fg: "#93c5fd", mark: "✓" },
  };
  const lift = (r) => Math.min(r * 3, 14); // retries raise + enlarge the node

  return (
    <div style={box}>
      <h3 style={{ color: C.text, marginBottom: 4 }}>Beam-search tree — {n} path(s)</h3>
      <p style={{ color: C.dim, fontSize: 12, marginBottom: 8 }}>
        Active path in blue, archived branches in gray · node size ∝ retries · hover a node for why it was eliminated
      </p>
      <svg viewBox={`0 0 ${W} ${leafBase + 64}`} width="100%" style={{ maxWidth: W }}>
        {nodes.map((node, i) => {
          const s = STATUS[node.status];
          const ly = leafBase - lift(node.retries);
          return (
            <g key={`edge-${i}`}>
              <line
                x1={rootX} y1={rootY + 14} x2={xs[i]} y2={ly - 14 - lift(node.retries)}
                stroke={s.stroke} strokeWidth={node.status === "active" ? 2.5 : 1.2}
                strokeDasharray={node.status === "active" ? "0" : "4 3"}
              />
              <text x={(rootX + xs[i]) / 2} y={(rootY + ly) / 2} fill={C.dim} fontSize="11" textAnchor="middle">
                {node.conf}
              </text>
            </g>
          );
        })}
        <circle cx={rootX} cy={rootY} r="14" fill="#0b1220" stroke="#60a5fa" strokeWidth="2" />
        <text x={rootX} y={rootY + 5} fill="#60a5fa" fontSize="13" textAnchor="middle" fontWeight="700">⌖</text>
        <text x={rootX} y={rootY - 22} fill={C.sub} fontSize="11" textAnchor="middle">root query</text>
        {nodes.map((node, i) => {
          const s = STATUS[node.status];
          const r = 12 + lift(node.retries);
          const ly = leafBase - lift(node.retries);
          const label = node.label.length > 28 ? node.label.slice(0, 26) + "…" : node.label;
          return (
            <g key={`node-${i}`}>
              <title>{`${node.status}: ${node.reason}`}</title>
              <circle cx={xs[i]} cy={ly} r={r} fill={s.fill} stroke={s.stroke} strokeWidth="2" />
              <text x={xs[i]} y={ly + 5} fill={s.fg} fontSize="14" textAnchor="middle" fontWeight="700">{s.mark}</text>
              <text x={xs[i]} y={ly + r + 16} fill={s.fg} fontSize="12" textAnchor="middle">{label}</text>
              <text x={xs[i]} y={ly + r + 31} fill={C.dim} fontSize="10.5" textAnchor="middle">
                {node.conf}{node.retries ? ` · ${node.retries} retr.` : ""}
              </text>
            </g>
          );
        })}
      </svg>
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

function ReviewGate({ sessionId, state, busy, onDecision }) {
  const payload = state.review_payload;
  if (state.status !== "AWAITING_REVIEW" || !payload) return null;

  const isSample = (state.query || "").startsWith("[SAMPLE]");
  const btnStyle = {
    borderRadius: 6,
    padding: "10px 16px",
    fontSize: 15,
    fontWeight: 600,
    border: "none",
  };

  return (
    <div style={box}>
      <h3 style={{ color: C.text, marginBottom: 8 }}>Human review</h3>
      <p style={{ color: C.sub, fontSize: 14, marginBottom: 8 }}>
        {payload.summary || "Review the proposed remediation before proceeding."}
      </p>
      {payload.root_cause && (
        <p style={{ color: C.dim, fontSize: 13, marginBottom: 12 }}>{payload.root_cause}</p>
      )}
      {(payload.remediation || []).map((cmd, i) => (
        <pre key={i} style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 6,
          padding: "8px 12px", color: "#7dd3fc", fontSize: 12.5, overflowX: "auto", marginBottom: 6 }}>
          {cmd}
        </pre>
      ))}
      {isSample ? (
        <p style={{ color: C.dim, fontSize: 13, marginTop: 10 }}>
          Sample session: review actions are disabled because this journey is a recorded snapshot.
        </p>
      ) : (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
          <button
            onClick={() => onDecision("approve")}
            disabled={busy || !sessionId}
            style={{ ...btnStyle, background: busy ? C.dim : "#166534", color: "#fff", cursor: busy ? "default" : "pointer" }}
          >
            {busy ? "Submitting…" : "Approve remediation"}
          </button>
          <button
            onClick={() => onDecision("reject")}
            disabled={busy || !sessionId}
            style={{ ...btnStyle, background: busy ? C.dim : "#991b1b", color: "#fff", cursor: busy ? "default" : "pointer" }}
          >
            Reject remediation
          </button>
        </div>
      )}
    </div>
  );
}

// B9: groups the introspection sections (collectors → beam tree → timeline →
// paths) and subscribes to the /stream SSE endpoint, so the panel refreshes in
// real time while a session is running — each SSE event pulls a fresh snapshot.
// Falls back silently to the parent's polling if EventSource can't connect.
function IntrospectionPanel({ sessionId, state, live, onUpdate }) {
  useEffect(() => {
    if (!sessionId || !live) return undefined;
    let es;
    try {
      es = new EventSource(`/api/v1/sessions/${sessionId}/stream`);
    } catch {
      return undefined;
    }
    es.onmessage = () => {
      getState(sessionId).then((s) => onUpdate?.(s)).catch(() => {});
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [sessionId, live, onUpdate]);

  return (
    <>
      <FallbackStatus state={state} />
      <BeamTree state={state} />
      <Timeline state={state} />
      <Paths state={state} />
    </>
  );
}

export default function DecisionJourney() {
  const [query, setQuery] = useState("pods crashlooping in production");
  const [namespace, setNamespace] = useState("");
  const [token, setTok] = useState(getToken());
  const [sessionId, setSessionId] = useState("");
  const [state, setState] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | running | done | error
  const [error, setError] = useState("");

  async function go(action) {
    setToken(token.trim());
    setError(""); setState(null); setStatus("running");
    try {
      const { session_id, state: final } = await action();
      setSessionId(session_id || "");
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

  async function decide(humanDecision) {
    if (!sessionId) return;
    setError("");
    setStatus("running");
    try {
      const { state: final } = await reviewSession(sessionId, humanDecision, { onTick: setState });
      setState(final);
      setStatus("done");
    } catch (e) {
      setError(String(e.message || e));
      setStatus("error");
    }
  }

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
            <EvidencePanel state={state} />
            <ReviewGate sessionId={sessionId} state={state} busy={status === "running"} onDecision={decide} />
            <IntrospectionPanel
              sessionId={sessionId}
              state={state}
              live={status === "running"}
              onUpdate={setState}
            />
            <RootCause state={state} />
          </>
        )}
      </div>
    </div>
  );
}
