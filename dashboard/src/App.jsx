import { useState } from "react";

const CATEGORIES = [
  {
    name: "Code Quality",
    max: 100,
    criteria: [
      { name: "Dependency injection (LLM, store)", current: 14, target: 18, max: 20 },
      { name: "Type safety (mypy)", current: 10, target: 16, max: 20 },
      { name: "Cyclomatic complexity", current: 12, target: 15, max: 20 },
      { name: "Dead code / vulture", current: 14, target: 16, max: 20 },
      { name: "Structured logging", current: 6, target: 15, max: 20 },
    ],
  },
  {
    name: "Operational Maturity",
    max: 100,
    criteria: [
      { name: "FAISS persistence (survives restarts)", current: 14, target: 18, max: 20 },
      { name: "Retry / timeout on K8s API calls", current: 8, target: 15, max: 20 },
      { name: "Index TTL / refresh strategy", current: 6, target: 14, max: 20 },
      { name: "Live cluster tested (k3d/kind)", current: 4, target: 16, max: 20 },
      { name: "Helm false-positive rate", current: 6, target: 14, max: 20 },
    ],
  },
  {
    name: "Business Value",
    max: 100,
    criteria: [
      { name: "RCA precision measured", current: 0, target: 14, max: 20 },
      { name: "Remediation active (not just print)", current: 4, target: 14, max: 20 },
      { name: "Human gate implemented", current: 14, target: 16, max: 20 },
      { name: "Air-gapped / data sovereignty", current: 18, target: 18, max: 20 },
      { name: "Time-to-value < 30 min", current: 8, target: 14, max: 20 },
    ],
  },
];

const ROADMAP = [
  {
    label: "Today",
    tag: "v0.2",
    delta: 0,
    notes: "Baseline — README, demo, persistence shipped",
  },
  {
    label: "Code quality",
    tag: "v0.3",
    delta: 18,
    notes: "mypy strict + structlog + full DI on LLMClient",
  },
  {
    label: "Ops ready",
    tag: "v0.4",
    delta: 19,
    notes: "Retry/timeout on K8s API + index TTL + k3d live test",
  },
  {
    label: "Active remediation",
    tag: "v0.5",
    delta: 16,
    notes: "LangGraph execute node + human gate + rollback",
  },
  {
    label: "RCA benchmark",
    tag: "v0.6",
    delta: 14,
    notes: "Precision/recall on 4 real incident types",
  },
];

const BASE_SCORE = CATEGORIES.flatMap((c) => c.criteria).reduce(
  (s, c) => s + c.current,
  0
);
const MAX_SCORE = CATEGORIES.flatMap((c) => c.criteria).reduce(
  (s, c) => s + c.max,
  0
);

function grade(score) {
  const pct = score / MAX_SCORE;
  if (pct >= 0.9) return { label: "A", color: "#21c354" };
  if (pct >= 0.75) return { label: "B", color: "#4ade80" };
  if (pct >= 0.6) return { label: "C", color: "#facc15" };
  if (pct >= 0.45) return { label: "D", color: "#fb923c" };
  return { label: "F", color: "#f87171" };
}

function Bar({ value, max, color }) {
  const pct = Math.round((value / max) * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div
        style={{
          flex: 1,
          height: 8,
          background: "#1e293b",
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: color,
            borderRadius: 4,
            transition: "width 0.4s ease",
          }}
        />
      </div>
      <span style={{ fontSize: 12, color: "#94a3b8", minWidth: 36 }}>
        {value}/{max}
      </span>
    </div>
  );
}

export default function App() {
  const [step, setStep] = useState(0);

  const score =
    BASE_SCORE + ROADMAP.slice(1, step + 1).reduce((s, r) => s + r.delta, 0);
  const g = grade(score);
  const pct = Math.round((score / MAX_SCORE) * 100);

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0f172a",
        color: "#e2e8f0",
        fontFamily: "'Inter', system-ui, sans-serif",
        padding: "2rem",
      }}
    >
      {/* Header */}
      <div style={{ maxWidth: 860, margin: "0 auto" }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "#f1f5f9", marginBottom: 4 }}>
          KubeWhisperer — Evaluation Score
        </h1>
        <p style={{ color: "#64748b", fontSize: 13, marginBottom: 32 }}>
          Objective maturity tracking across code quality, ops readiness, and business value.
        </p>

        {/* Score hero */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 32,
            background: "#1e293b",
            borderRadius: 12,
            padding: "24px 32px",
            marginBottom: 32,
          }}
        >
          <div style={{ textAlign: "center" }}>
            <div
              style={{
                fontSize: 56,
                fontWeight: 800,
                color: g.color,
                lineHeight: 1,
              }}
            >
              {g.label}
            </div>
            <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
              grade
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontSize: 36,
                fontWeight: 700,
                color: "#f1f5f9",
                lineHeight: 1,
              }}
            >
              {score}
              <span style={{ fontSize: 18, color: "#64748b" }}>/{MAX_SCORE}</span>
            </div>
            <div style={{ marginTop: 12 }}>
              <div
                style={{
                  height: 10,
                  background: "#0f172a",
                  borderRadius: 5,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${pct}%`,
                    height: "100%",
                    background: g.color,
                    borderRadius: 5,
                    transition: "width 0.5s ease",
                  }}
                />
              </div>
              <div
                style={{ fontSize: 12, color: "#64748b", marginTop: 6 }}
              >
                {pct}% — {ROADMAP[step].label} ({ROADMAP[step].tag})
              </div>
            </div>
          </div>
        </div>

        {/* Roadmap stepper */}
        <div style={{ display: "flex", gap: 8, marginBottom: 32, flexWrap: "wrap" }}>
          {ROADMAP.map((r, i) => (
            <button
              key={i}
              onClick={() => setStep(i)}
              style={{
                padding: "6px 14px",
                borderRadius: 6,
                border: "none",
                cursor: "pointer",
                fontSize: 13,
                fontWeight: 600,
                background: i === step ? g.color : "#1e293b",
                color: i === step ? "#0f172a" : "#94a3b8",
                transition: "all 0.2s",
              }}
            >
              {r.tag} {r.label}
            </button>
          ))}
        </div>

        {/* Current step note */}
        <div
          style={{
            background: "#1e293b",
            borderLeft: `3px solid ${g.color}`,
            borderRadius: "0 8px 8px 0",
            padding: "12px 16px",
            fontSize: 13,
            color: "#94a3b8",
            marginBottom: 32,
          }}
        >
          {step > 0 && (
            <span style={{ color: g.color, fontWeight: 600 }}>
              +{ROADMAP[step].delta} pts —{" "}
            </span>
          )}
          {ROADMAP[step].notes}
        </div>

        {/* Categories */}
        <div style={{ display: "grid", gap: 24 }}>
          {CATEGORIES.map((cat) => {
            const catScore = cat.criteria.reduce((s, c) => s + c.current, 0);
            return (
              <div
                key={cat.name}
                style={{
                  background: "#1e293b",
                  borderRadius: 10,
                  padding: "20px 24px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: 16,
                  }}
                >
                  <h2 style={{ fontSize: 15, fontWeight: 700, color: "#f1f5f9", margin: 0 }}>
                    {cat.name}
                  </h2>
                  <span style={{ fontSize: 13, color: "#64748b" }}>
                    {catScore}/{cat.max}
                  </span>
                </div>
                <div style={{ display: "grid", gap: 12 }}>
                  {cat.criteria.map((c) => (
                    <div key={c.name}>
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          fontSize: 13,
                          color: "#94a3b8",
                          marginBottom: 4,
                        }}
                      >
                        <span>{c.name}</span>
                      </div>
                      <Bar value={c.current} max={c.max} color={g.color} />
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        <p style={{ marginTop: 32, fontSize: 12, color: "#334155", textAlign: "center" }}>
          Scores are manually updated as capabilities ship. Source of truth: code + CI.
        </p>
      </div>
    </div>
  );
}
