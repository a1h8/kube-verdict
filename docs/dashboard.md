# Dashboards

KubeVerdict has 3 dashboards, each answering a different question, linked into one
narrative vitrine rather than left as isolated tools (B15, `docs/roadmap.md`):

| Dashboard | Question it answers | Stack | Status |
|---|---|---|---|
| **Evaluation Score** | How mature is KubeVerdict overall? | React + Vite, GitHub Pages | Built |
| **Decision Journey** (`#/journey`) | Why did this specific session reach this verdict? | React, consumes the API, SSE live | Built, incl. evidence panel (logs/traces behind each hypothesis) — B15 |
| **Monitoring Ops** | Is the KubeVerdict service itself healthy right now? | Grafana reading OTLP (metrics + traces exported by `telemetry.py`) | Instrumentation built (B15); Grafana dashboard itself pending, needs real infra |

Not to be confused with PatchTST's own validation dashboard (`tools/render_dashboard.py`,
Axis 3 in `kubeverdict-patchtst-map.md`) — that one lives in the PatchTST repo and stays
there; it is not one of KubeVerdict's 3.

This page documents the **Evaluation Score** dashboard. See `docs/ui.md` for the Streamlit
UI's own internal "Dashboard" tab (ingestion pipeline stats) — a debugging surface, not one
of the 3 above.

---

# Evaluation Score Dashboard

An interactive maturity tracker for KubeVerdict — built with React + Vite, deployed automatically to GitHub Pages on every push to `main`.

**Live:** https://a1h8.github.io/KubeVerdict/

---

## Purpose

The dashboard tracks objective progress across three dimensions:

- **Code Quality** — dependency injection, type safety, complexity, dead code, structured logging
- **Operational Maturity** — persistence, retry/timeout, index TTL, live cluster tested, false-positive rate
- **Business Value** — RCA precision measured, active remediation, human gate, air-gapped, time-to-value

Each criterion is scored manually against what is verifiable in code and CI — not self-reported features.

---

## Run locally

```bash
cd dashboard
npm install
npm run dev   # → http://localhost:5173
```

---

## Update scores

All scores live in `dashboard/src/App.jsx` in the `CATEGORIES` array. Each criterion has two fields:

```js
{ name: "FAISS persistence (survives restarts)", current: 14, target: 18, max: 20 }
```

| Field | Meaning |
|---|---|
| `current` | Score today — update when the capability ships |
| `target` | Score after the next roadmap step |
| `max` | Maximum possible score for this criterion |

When a capability ships, increment `current` and re-run the build. The grade and progress bar update automatically.

---

## Roadmap steps

The stepper at the top of the dashboard shows projected scores for each milestone:

| Step | Tag | Delta | Focus |
|---|---|---|---|
| Today | v0.2 | — | Baseline: README, demo, persistence |
| Code quality | v0.3 | +18 | mypy strict + structlog + full DI |
| Ops ready | v0.4 | +19 | Retry/timeout + index TTL + k3d live test |
| Active remediation | v0.5 | +16 | LangGraph execute node + human gate + rollback |
| RCA benchmark | v0.6 | +14 | Precision/recall on 4 real incident types |

---

## Grading scale

| Grade | Score | Meaning |
|---|---|---|
| A | ≥ 90% | Production-ready |
| B | ≥ 75% | Credible for enterprise evaluation |
| C | ≥ 60% | Solid POC |
| D | ≥ 45% | Early prototype |
| F | < 45% | Not yet demonstrable |

Current score: **188/300 (D)** — credible demo, gaps in ops maturity and measured RCA precision.

---

## Deployment

GitHub Actions builds and deploys the dashboard automatically on every push to `main` that touches `dashboard/**`.

Workflow: `.github/workflows/dashboard.yml`

To activate GitHub Pages the first time:
1. Go to **Settings → Pages**
2. Set **Source** to **GitHub Actions**
3. Push any change under `dashboard/` — the workflow handles the rest
