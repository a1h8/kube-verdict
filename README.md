# KubeWhisperer

> Correlate Kubernetes signals, detect GitOps drift, and get validated remediation patches — with a human approval gate before anything touches production.

[![CI](https://github.com/a1h8/KubeWhisperer/actions/workflows/ci.yml/badge.svg)](https://github.com/a1h8/KubeWhisperer/actions/workflows/ci.yml)
[![Validated cases](https://img.shields.io/badge/validated%20cases-h001--h006-blue)](#validated-scenarios)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)

KubeWhisperer is designed to correlate Kubernetes events, Helm drift, Prometheus alerts, OTel traces and Loki logs. The current CI demo validates the deterministic RCA pipeline offline on Kubernetes/Helm incident fixtures — no live cluster, no LLM required.

**By default it runs entirely local** — Ollama + Mistral, no data leaves your infrastructure. Cloud providers (Groq, Anthropic, OpenAI, Google Gemini) are drop-in replacements via `LLM_PROVIDER`.

```
Signals → Correlation → Hypotheses → Dry-run validation → Human gate → GitOps patch
```

---

## Why it matters

Kubernetes incidents are rarely single-signal failures. KubeWhisperer separates root causes from cascades, detects GitOps drift between Helm values and running state, proposes a safe remediation path, and keeps a human approval gate before production changes.

## What is proven today

| Capability | Status |
|---|---|
| Offline deterministic RCA pipeline | Proven in CI (h001–h006) |
| Multi-signal collectors (Prometheus, OTel, Loki) | Implemented / configurable |
| Live cluster usage | Available with kubeconfig |
| GitOps patching | Human-gated, dry-run first |

---

## Demo

![KubeWhisperer demo](demo/demo_kubeWhisperer.gif)

No real Kubernetes cluster required. The demo runs entirely offline against a pre-built incident scenario.

The scenario injects three independent root causes and one cascading failure:

| Service | Failure | Root cause |
|---|---|---|
| `db-primary` | 0 replicas | Helm drift — chart declares `replicas: 1`, cluster running `replicas: 0` |
| `payment-api` | CrashLoopBackOff | Cascade — DB connection refused (db-primary has 0 endpoints) |
| `analytics-worker` | OOMKilled | Memory limit drift: deployed 50Mi vs Helm chart 256Mi |
| `notification-svc` | ImagePullBackOff | Image tag drift: manifest `v3.2.1`, cluster resolved `:latest` (removed) |
| `ml-inference` | Pending | GPU scheduling delay — resolves automatically once capacity frees |
| `api-gateway` | Running ✓ | Healthy baseline |

**What the analysis produces:**
- Root causes ranked by evidence weight, cascades identified separately
- Git diffs computed from Helm/manifest anchor annotations — not hardcoded
- Immediate kubectl mitigation + GitOps remediation split explicitly
- Dry-run validated before display, human review gate before apply

```bash
# Default: LLM_PROVIDER=ollama (local, no data leaves infra)
# Alternatives: groq | anthropic | openai | google | demo (offline)
bash demo/kap_record.sh   # starts Streamlit + opens browser
```

---

## Quick start

**Prerequisites:** Python 3.11+, a Kubernetes cluster reachable via kubeconfig, and one LLM provider configured in `.env`.

```bash
git clone https://github.com/a1h8/KubeWhisperer.git
cd KubeWhisperer
pip install -r requirements.txt

cp .env.example .env
# Edit .env: KUBECONFIG, LLM_PROVIDER, KUBE_NAMESPACES
# LLM_PROVIDER=ollama  → ollama pull mistral  (local, no data leaves infra)
# LLM_PROVIDER=groq    → set GROQ_API_KEY     (fast, free tier)
# LLM_PROVIDER=anthropic|openai|google → set corresponding API key

streamlit run ui/app.py
```

### Try without a cluster

The **Integration Tests** tab runs entirely offline — no cluster, no Ollama needed:

1. `streamlit run ui/app.py`
2. Go to **🧪 Integration Tests**
3. Select any `h00N_*` case from the dropdown
4. Mode defaults to **🔬 Pipeline trace** — pipeline runs automatically
5. Explore all 10 steps: tokenizer → retrieval → anchors → drift → confidence → proposed fixes

---

## Validated scenarios

Six failure patterns proven end-to-end in CI — no cluster, no Ollama required.

| Scenario | Case | What it proves |
|---|---|---|
| CrashLoopBackOff — missing dependency | h001 | BFS graph traversal, BM25+FAISS retrieval, anchor detection, confidence scoring, fix proposals |
| ImagePullBackOff — registry auth / tag drift | h002 | Helm drift detection, `drift.*` annotations, image proposal generation |
| OOMKilled — memory limit drift | h003 | Helm declared-vs-observed diff, `anchor_fix_hints()` → `helm upgrade --set` |
| Missing ConfigMap / Secret at pod start | h004 | `DeploymentReadinessDetector`, `missing.*` annotations, `kubectl create` hints |
| NetworkPolicy egress block | h005 | `netpol.*` annotations, `kubectl edit networkpolicy` hints |
| RBAC — missing ClusterRoleBinding | h006 | SA exists but no binding detected, `kubectl create clusterrolebinding` hint |

Each case runs the full pre-LLM pipeline: graph construction → hybrid retrieval (BM25 + FAISS + RRF) → context building → anchor/drift/policy scoring → proposal generation.

---

## How it works

The LLM is constrained by retrieved evidence. KubeWhisperer ranks hypotheses from deterministic signals first — ontology topology, anchor violations, drift, policies and resolved incidents — then uses the LLM only to produce an evidence-grounded RCA.

Confidence routing uses beam search: two consecutive LOW results on the same hypothesis path trigger an immediate switch to the next candidate, and archived paths re-rank remaining candidates using signals from the failed analysis.

**Pipeline:**

```
K8s events + Prometheus + OTel/Loki + Helm values
        ↓
Ontology graph + anchor drift detection
        ↓
BM25 + FAISS + RRF hybrid retrieval
        ↓
Beam-search hypothesis ranking
        ↓
LLM root-cause analysis (evidence-grounded)
        ↓
Dry-run validation → human review gate → GitOps patch
```

---

## Documentation

| Document | Content |
|---|---|
| [Architecture](docs/architecture.md) | Full pipeline diagram, LangGraph workflow, evidence-first hypothesis generation, beam search routing, anchor system design, drift detection, PatchTST |
| [UI reference](docs/ui.md) | Streamlit tabs, pipeline trace steps, anchor pivot table, reasoning journey, router decisions |
| [Test cases](docs/test-cases.md) | h001–h006 validated scenarios, case format, adding a new case, CI coverage |
| [Project layout](docs/project-layout.md) | Full directory tree, RBAC |
| [Roadmap](docs/roadmap.md) | Done and next |
| [Configuration](docs/configuration.md) | All `.env` variables, hybrid retrieval tuning, source weights |
| [Deployment](docs/deployment.md) | Docker, k3d, production K8s |

---

## License

[Apache 2.0](LICENSE)
