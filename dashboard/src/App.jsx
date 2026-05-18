import { useState } from "react";

// ── constants ──────────────────────────────────────────────────────────────────

const GITHUB = "https://github.com/a1h8/KubeWhisperer";
const GIF_URL =
  "https://raw.githubusercontent.com/a1h8/KubeWhisperer/main/demo/shell_demo.gif";

const SCENARIOS = [
  {
    id: "h001",
    title: "CrashLoopBackOff",
    desc: "Missing dependency. BFS graph traversal, BM25+FAISS retrieval, anchor detection, confidence scoring.",
  },
  {
    id: "h002",
    title: "ImagePullBackOff",
    desc: "Registry auth / tag drift. Helm drift detection, drift.* annotations, image proposal generation.",
  },
  {
    id: "h003",
    title: "OOMKilled",
    desc: "Memory limit drift. Helm declared-vs-observed diff, anchor_fix_hints() → helm upgrade --set.",
  },
  {
    id: "h004",
    title: "Missing ConfigMap / Secret",
    desc: "Resource missing at pod start. DeploymentReadinessDetector, missing.* annotations, kubectl create hints.",
  },
  {
    id: "h005",
    title: "NetworkPolicy egress block",
    desc: "Blocked egress traffic. netpol.* annotations, kubectl edit networkpolicy hints.",
  },
  {
    id: "h006",
    title: "RBAC — missing ClusterRoleBinding",
    desc: "SA exists but no binding. Detected via SA→role graph, kubectl create clusterrolebinding hint.",
  },
];

const PIPELINE_STEPS = [
  "K8s events + Prometheus + OTel/Loki + Helm values",
  "Ontology graph + anchor drift detection",
  "BM25 + FAISS + RRF hybrid retrieval",
  "Beam-search hypothesis ranking",
  "LLM root-cause analysis (evidence-grounded)",
  "Dry-run validation → human review gate → GitOps patch",
];

const LIMITATIONS = [
  "Validated cases: h001–h006 only — h007–h012+ are roadmap, not yet shipped.",
  "Single-cluster — multi-cluster not yet wired end-to-end.",
  "No auto-remediation in production — human approval gate is by design.",
  "LLM performance is local-hardware-dependent (Mistral via Ollama, M-series Mac optimal).",
  "No real-time alerting integration — Prometheus and Loki are pulled on demand, not streamed.",
];

const INCIDENT_SUMMARY = `════════════════════════════════════════════════════════════
  INCIDENT SUMMARY
════════════════════════════════════════════════════════════
  Severity    : HIGH
  Namespace   : kubewhisperer-demo
  Confidence  : MEDIUM
  Impacted    : payment-service, ml-inference, notification-service

  Root cause  :
    payment-service is in CrashLoopBackOff due to repeated
    container failures. ml-inference cannot pull its image
    (ImagePullBackOff). notification-service is missing a
    required environment variable.

  Key evidence:
    • [447×] BackOff on Pod/payment-service-58555ff9b6-4bxv2
      "Back-off restarting failed container payment-service"
    • [  1×] Failed on Pod/ml-inference-6c7dbf6d5f-2nlsr
      "Failed to pull image: not found"

  Proposed fix:
    $ kubectl rollout restart deployment/payment-service
    $ kubectl set image deployment/ml-inference \\
        ml-inference=<correct-image>
════════════════════════════════════════════════════════════
  Approve and apply remediation? [approve/reject]: approve
  ✓ Remediation approved — commands above should be applied.`;

// ── score data ─────────────────────────────────────────────────────────────────

const CATEGORIES = [
  {
    name: "Code Quality",
    max: 100,
    criteria: [
      { name: "Dependency injection (LLM, store)", current: 14, max: 20 },
      { name: "Type safety (mypy)", current: 10, max: 20 },
      { name: "Cyclomatic complexity", current: 12, max: 20 },
      { name: "Dead code / vulture", current: 14, max: 20 },
      { name: "Structured logging", current: 6, max: 20 },
    ],
  },
  {
    name: "Operational Maturity",
    max: 100,
    criteria: [
      { name: "FAISS persistence (survives restarts)", current: 14, max: 20 },
      { name: "Retry / timeout on K8s API calls", current: 8, max: 20 },
      { name: "Index TTL / refresh strategy", current: 6, max: 20 },
      { name: "Live cluster tested (k3d/kind)", current: 4, max: 20 },
      { name: "Helm false-positive rate", current: 6, max: 20 },
    ],
  },
  {
    name: "Business Value",
    max: 100,
    criteria: [
      { name: "RCA precision measured", current: 0, max: 20 },
      { name: "Remediation active (not just print)", current: 14, max: 20 },
      { name: "Human gate implemented", current: 14, max: 20 },
      { name: "Air-gapped / data sovereignty", current: 18, max: 20 },
      { name: "Time-to-value < 30 min", current: 8, max: 20 },
    ],
  },
];

const ALL_CRITERIA = CATEGORIES.flatMap((c) => c.criteria);
const SCORE = ALL_CRITERIA.reduce((s, c) => s + c.current, 0);
const MAX_SCORE = ALL_CRITERIA.reduce((s, c) => s + c.max, 0);

function grade(score) {
  const pct = score / MAX_SCORE;
  if (pct >= 0.9) return { label: "A", color: "#21c354" };
  if (pct >= 0.75) return { label: "B", color: "#4ade80" };
  if (pct >= 0.6) return { label: "C", color: "#facc15" };
  if (pct >= 0.45) return { label: "D", color: "#fb923c" };
  return { label: "F", color: "#f87171" };
}

// ── style helpers ──────────────────────────────────────────────────────────────

const T = {
  page: {
    minHeight: "100vh",
    background: "#0f172a",
    color: "#e2e8f0",
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  wrap: { maxWidth: 860, margin: "0 auto", padding: "0 1.5rem" },
  section: { padding: "4rem 0" },
  h2: { fontSize: 22, fontWeight: 700, color: "#f1f5f9", marginBottom: 8 },
  sub: { fontSize: 14, color: "#64748b", marginBottom: 32 },
  card: {
    background: "#1e293b",
    borderRadius: 10,
    padding: "20px 24px",
  },
  badge: {
    display: "inline-block",
    fontSize: 11,
    fontWeight: 700,
    padding: "2px 8px",
    borderRadius: 4,
    background: "#0f172a",
    color: "#38bdf8",
    border: "1px solid #1e3a5f",
    marginBottom: 8,
  },
};

function Bar({ value, max, color }) {
  const pct = Math.round((value / max) * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div
        style={{
          flex: 1,
          height: 6,
          background: "#0f172a",
          borderRadius: 3,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: color,
            borderRadius: 3,
            transition: "width 0.4s ease",
          }}
        />
      </div>
      <span style={{ fontSize: 11, color: "#64748b", minWidth: 36 }}>
        {value}/{max}
      </span>
    </div>
  );
}

function Btn({ href, children, primary, external = true }) {
  const [hovered, setHovered] = useState(false);
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      style={{
        display: "inline-block",
        padding: "10px 22px",
        borderRadius: 8,
        fontSize: 14,
        fontWeight: 600,
        textDecoration: "none",
        background: primary ? "#3b82f6" : "#1e293b",
        color: primary ? "#fff" : "#94a3b8",
        border: primary ? "none" : "1px solid #334155",
        opacity: hovered ? 0.85 : 1,
        transition: "opacity 0.15s",
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {children}
    </a>
  );
}

// ── sections ───────────────────────────────────────────────────────────────────

function Hero() {
  return (
    <div
      style={{
        ...T.section,
        borderBottom: "1px solid #1e293b",
        paddingBottom: "3rem",
      }}
    >
      <div style={T.wrap}>
        <div style={{ ...T.badge, marginBottom: 16 }}>
          Portfolio-grade AI Ops prototype
        </div>
        <h1
          style={{
            fontSize: 36,
            fontWeight: 800,
            color: "#f1f5f9",
            lineHeight: 1.2,
            marginBottom: 16,
          }}
        >
          KubeWhisperer
        </h1>
        <p
          style={{
            fontSize: 18,
            color: "#94a3b8",
            marginBottom: 8,
            maxWidth: 620,
            lineHeight: 1.6,
          }}
        >
          AI-assisted Kubernetes incident analysis with evidence-grounded RCA
          and human-gated remediation.
        </p>
        <p
          style={{
            fontSize: 14,
            color: "#475569",
            marginBottom: 32,
            maxWidth: 580,
          }}
        >
          Correlates Kubernetes events, Helm drift, Prometheus alerts, OTel
          traces and Loki logs to reduce incident investigation time.
        </p>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <Btn href="#demo" primary external={false}>
            Watch demo
          </Btn>
          <Btn href={GITHUB}>View GitHub</Btn>
          <Btn href={`${GITHUB}/blob/main/docs/test-cases.md`}>
            Run offline test cases
          </Btn>
        </div>
      </div>
    </div>
  );
}

function WhyItMatters() {
  return (
    <div style={{ ...T.section, borderBottom: "1px solid #1e293b" }}>
      <div style={T.wrap}>
        <h2 style={T.h2}>Why it matters</h2>
        <p
          style={{
            fontSize: 15,
            color: "#94a3b8",
            lineHeight: 1.7,
            maxWidth: 680,
          }}
        >
          Most Kubernetes outages are not caused by a single failing pod. When
          payment-service crashes, the on-call engineer opens five tabs
          simultaneously: pod logs, Kubernetes events, Helm history, Prometheus
          graphs, and the GitOps repo — at 2 AM, under pressure, with three
          Slack threads open. The root cause is rarely where the alert fired.
          <br />
          <br />
          KubeWhisperer reduces that cognitive load. It correlates signals into
          a single evidence-grounded root cause analysis, ranked by confidence,
          with a human approval gate before any remediation command touches
          production.
        </p>
      </div>
    </div>
  );
}

function DemoSection() {
  return (
    <div
      id="demo"
      style={{ ...T.section, borderBottom: "1px solid #1e293b" }}
    >
      <div style={T.wrap}>
        <h2 style={T.h2}>What the output looks like</h2>
        <p style={T.sub}>
          Five services down simultaneously. KubeWhisperer identifies each root
          cause independently, ranked by evidence weight.
        </p>

        <pre
          style={{
            background: "#020617",
            border: "1px solid #1e293b",
            borderRadius: 10,
            padding: "20px 24px",
            fontSize: 12.5,
            lineHeight: 1.7,
            color: "#94a3b8",
            overflowX: "auto",
            marginBottom: 32,
            whiteSpace: "pre",
          }}
        >
          {INCIDENT_SUMMARY}
        </pre>

        <img
          src={GIF_URL}
          alt="KubeWhisperer shell demo"
          style={{
            width: "100%",
            borderRadius: 10,
            border: "1px solid #1e293b",
          }}
        />
      </div>
    </div>
  );
}

function Scenarios() {
  return (
    <div style={{ ...T.section, borderBottom: "1px solid #1e293b" }}>
      <div style={T.wrap}>
        <h2 style={T.h2}>Validated scenarios</h2>
        <p style={T.sub}>
          Six failure patterns proven end-to-end in CI — no cluster, no LLM
          required.
        </p>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
            gap: 16,
          }}
        >
          {SCENARIOS.map((s) => (
            <div key={s.id} style={T.card}>
              <div style={{ ...T.badge, marginBottom: 8 }}>{s.id}</div>
              <div
                style={{
                  fontSize: 15,
                  fontWeight: 700,
                  color: "#f1f5f9",
                  marginBottom: 8,
                }}
              >
                {s.title}
              </div>
              <div style={{ fontSize: 12.5, color: "#64748b", lineHeight: 1.6 }}>
                {s.desc}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function HowItWorks() {
  return (
    <div style={{ ...T.section, borderBottom: "1px solid #1e293b" }}>
      <div style={T.wrap}>
        <h2 style={T.h2}>How it works</h2>
        <p style={T.sub}>
          The LLM is constrained by retrieved evidence — deterministic signals
          rank hypotheses first.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
          {PIPELINE_STEPS.map((step, i) => (
            <div
              key={i}
              style={{ display: "flex", alignItems: "flex-start", gap: 16 }}
            >
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                }}
              >
                <div
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: "50%",
                    background: "#1e293b",
                    border: "2px solid #334155",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 11,
                    fontWeight: 700,
                    color: "#38bdf8",
                    flexShrink: 0,
                  }}
                >
                  {i + 1}
                </div>
                {i < PIPELINE_STEPS.length - 1 && (
                  <div
                    style={{
                      width: 1,
                      height: 24,
                      background: "#1e3a5f",
                      margin: "2px 0",
                    }}
                  />
                )}
              </div>
              <div
                style={{
                  fontSize: 14,
                  color: "#94a3b8",
                  paddingTop: 5,
                  paddingBottom: i < PIPELINE_STEPS.length - 1 ? 0 : 0,
                  lineHeight: 1.5,
                }}
              >
                {step}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Limitations() {
  return (
    <div style={{ ...T.section, borderBottom: "1px solid #1e293b" }}>
      <div style={T.wrap}>
        <h2 style={T.h2}>Current limitations</h2>
        <p style={T.sub}>
          This is a portfolio-grade prototype. These constraints are known and
          intentional — not hidden.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {LIMITATIONS.map((l, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: 12,
                fontSize: 14,
                color: "#64748b",
                lineHeight: 1.6,
              }}
            >
              <span style={{ color: "#fb923c", flexShrink: 0, marginTop: 1 }}>
                →
              </span>
              {l}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ProjectMaturity() {
  const g = grade(SCORE);
  const pct = Math.round((SCORE / MAX_SCORE) * 100);

  return (
    <div style={{ ...T.section, borderBottom: "1px solid #1e293b" }}>
      <div style={T.wrap}>
        <h2 style={T.h2}>Project maturity</h2>
        <p style={T.sub}>
          Objective self-assessment across code quality, ops readiness, and
          business value. Updated as capabilities ship.
        </p>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 32,
            ...T.card,
            marginBottom: 24,
          }}
        >
          <div style={{ textAlign: "center" }}>
            <div
              style={{
                fontSize: 48,
                fontWeight: 800,
                color: g.color,
                lineHeight: 1,
              }}
            >
              {g.label}
            </div>
            <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
              grade
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontSize: 32,
                fontWeight: 700,
                color: "#f1f5f9",
                lineHeight: 1,
              }}
            >
              {SCORE}
              <span style={{ fontSize: 16, color: "#64748b" }}>
                /{MAX_SCORE}
              </span>
            </div>
            <div style={{ marginTop: 10 }}>
              <div
                style={{
                  height: 8,
                  background: "#0f172a",
                  borderRadius: 4,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${pct}%`,
                    height: "100%",
                    background: g.color,
                    borderRadius: 4,
                  }}
                />
              </div>
              <div style={{ fontSize: 12, color: "#64748b", marginTop: 6 }}>
                {pct}%
              </div>
            </div>
          </div>
        </div>

        <div style={{ display: "grid", gap: 16 }}>
          {CATEGORIES.map((cat) => {
            const catScore = cat.criteria.reduce((s, c) => s + c.current, 0);
            return (
              <div key={cat.name} style={T.card}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: 14,
                  }}
                >
                  <span
                    style={{ fontSize: 14, fontWeight: 700, color: "#f1f5f9" }}
                  >
                    {cat.name}
                  </span>
                  <span style={{ fontSize: 12, color: "#64748b" }}>
                    {catScore}/{cat.max}
                  </span>
                </div>
                <div style={{ display: "grid", gap: 10 }}>
                  {cat.criteria.map((c) => (
                    <div key={c.name}>
                      <div
                        style={{
                          fontSize: 12,
                          color: "#64748b",
                          marginBottom: 4,
                        }}
                      >
                        {c.name}
                      </div>
                      <Bar value={c.current} max={c.max} color={g.color} />
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Footer() {
  return (
    <div style={{ padding: "2rem 0" }}>
      <div
        style={{
          ...T.wrap,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <span style={{ fontSize: 12, color: "#334155" }}>
          KubeWhisperer — portfolio-grade AI Ops prototype. Apache 2.0.
        </span>
        <div style={{ display: "flex", gap: 16 }}>
          <a
            href={GITHUB}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: 12, color: "#475569", textDecoration: "none" }}
          >
            GitHub
          </a>
          <a
            href={`${GITHUB}/blob/main/docs/architecture.md`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: 12, color: "#475569", textDecoration: "none" }}
          >
            Architecture
          </a>
          <a
            href={`${GITHUB}/blob/main/docs/api.md`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: 12, color: "#475569", textDecoration: "none" }}
          >
            REST API
          </a>
          <a
            href={`${GITHUB}/blob/main/docs/roadmap.md`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: 12, color: "#475569", textDecoration: "none" }}
          >
            Roadmap
          </a>
        </div>
      </div>
    </div>
  );
}

// ── app ────────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <div style={T.page}>
      <Hero />
      <WhyItMatters />
      <DemoSection />
      <Scenarios />
      <HowItWorks />
      <Limitations />
      <ProjectMaturity />
      <Footer />
    </div>
  );
}
