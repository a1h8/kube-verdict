# KubeWhisperer

> Automated Root Cause Analysis for Kubernetes — fully local, no data leaves your infrastructure.

[![CI](https://github.com/your-org/kubewhisperer/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/kubewhisperer/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-%3E70%25-brightgreen)](#tests)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

KubeWhisperer combines a typed Kubernetes ontology, a local FAISS vector store, and Mistral running on Ollama to diagnose cluster incidents — without sending any data to external services.

---

## How it works

```
K8s API server  ──┐
Helm releases   ──┼──► OntologyGraph ──► HelmDriftDetector
Helmfile        ──┘         │                    │
                            │            drift annotations
                            ▼
                      FAISSStore (all-MiniLM-L6-v2)
                            │
                   SignalAnalyzer (PatchTST)
                   ├── restart_count anomalies
                   ├── ready_ratio degradation
                   └── event_count spikes ──► signal.* annotations
                            │
                     ┌──────┴──────┐
                     │ Dedup chain │
                     │  BFS        │  (graph traversal from seeds)
                     │  Jaccard    │  (token-overlap dedup)
                     │  TF-IDF     │  (trigram ranking)
                     └──────┬──────┘
                            │
                     ContextWindow
                     ├── [CRITICAL] failed entities
                     ├── [CRITICAL] drift (Helm ≠ K8s)
                     ├── [SIGNALS]  PatchTST anomalies
                     ├── [WARNING]  events
                     ├── [Helm]     releases
                     └── [Related]  neighbours
                            │
                     Mistral via Ollama          ◄── retry on LOW confidence
                     (local, temperature=0.1)
                            │
                     human_review [INTERRUPT]    ◄── operator approves / rejects
                            │
                       RCAReport + remediation
                       ├── summary
                       ├── affected resources
                       ├── root cause
                       ├── causal chain
                       ├── remediation (kubectl / helm commands)
                       └── confidence
```

---

## Key properties

| Property | Detail |
|---|---|
| **Data sovereignty** | All inference runs locally — cluster data never leaves your network |
| **Air-gapped** | Works without internet once models and dependencies are pulled |
| **Ontology-aware** | Typed entities (Pod, Deployment, HelmRelease, …) with directed relationship edges |
| **Helm + Helmfile** | Correlates declared values with live runtime state; detects drift |
| **Dynamic discovery** | Queries `/apis` to index CRDs and operator resources automatically |
| **Multi-version K8s** | Detects server version at startup; drives API choices for 1.16 → 1.30+ and K3s |
| **Trigram TF-IDF** | K8s-aware tokenisation preserves `phase=Failed`, `apps/v1`, `v1.28.3+k3s1` |
| **PatchTST signals** | Forecasting-based anomaly detection on restart counts, readiness ratios, event spikes |
| **LangGraph workflow** | Stateful RCA graph with confidence-gated retry and human-in-the-loop approval interrupt |

---

## Quick start

**Prerequisites:** Python 3.11+, a Kubernetes cluster reachable via kubeconfig, Ollama with `mistral` pulled.

```bash
# Clone and set up
git clone https://github.com/your-org/kubewhisperer.git
cd kubewhisperer
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env — set KUBECONFIG, OLLAMA_URL, namespaces, etc.

# Pull the local model (one-time)
ollama pull mistral

# Run an analysis
python main.py --query "pods are crashlooping in namespace production" --stream

# Re-use an existing index (skip cluster collection)
python main.py --load-index --query "OOMKilled on worker nodes"
```

---

## Deploy on K3s (fully local stack)

The `scripts/init-k3s.sh` script installs K3s, deploys Ollama + Mistral, and schedules
KubeWhisperer as an hourly CronJob — all on one machine.

```bash
# Build the image
docker build -t ghcr.io/your-org/kubewhisperer:latest .

# Load into K3s (no registry needed)
docker save ghcr.io/your-org/kubewhisperer:latest | \
  sudo k3s ctr images import -

# Bootstrap everything
sudo bash scripts/init-k3s.sh --image ghcr.io/your-org/kubewhisperer:latest
```

For an existing cluster, apply the manifests directly:

```bash
kubectl create namespace kubewhisperer
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/ollama.yaml
kubectl apply -f k8s/kubewhisperer.yaml
```

See [docs/deployment.md](docs/deployment.md) for full options including GPU support and external Ollama.

---

## Tests

```bash
# All tests
pytest

# With coverage report
pytest --cov=. --cov-report=term-missing

# Unit only
pytest tests/unit/

# Integration only (no real cluster — uses synthetic graph + mock LLM)
pytest tests/integration/
```

Coverage is enforced at **>70%** in CI (`.github/workflows/ci.yml`).

---

## Project layout

```
kubewhisperer/
├── config.py                  # .env loader + typed config constants
├── main.py                    # CLI entry point
│
├── ontology/                  # K8s knowledge model
│   ├── entities.py            # Typed dataclasses: Pod, Deployment, HelmRelease, …
│   ├── graph.py               # OntologyGraph — nodes, edges, BFS
│   ├── relationships.py       # 14 edge types
│   ├── version.py             # KubeVersion + feature flags
│   ├── discovery.py           # Dynamic API server discovery (/apis)
│   └── dynamic_entity.py      # GenericEntity for CRDs
│
├── ingestion/                 # Cluster + Helm data collection
│   ├── k8s_collector.py       # Kubernetes API collector (version-aware)
│   ├── helm_collector.py      # helm get values / chart parsing
│   ├── helm_drift.py          # Helm declared vs K8s observed
│   ├── helmfile_collector.py  # Helmfile YAML parsing
│   └── chart_parser.py        # Chart.yaml, umbrella deps, value hierarchy
│
├── dedup/                     # Context deduplication
│   ├── bfs.py                 # Graph BFS from unhealthy seeds
│   ├── jaccard.py             # Token-level dedup
│   └── tfidf.py               # TF-IDF trigram ranking
│
├── vectorstore/               # Semantic search
│   ├── embedder.py            # sentence-transformers + L2 normalisation
│   └── store.py               # FAISSStore (IndexFlatIP, save/load)
│
├── signals/                   # Time-series anomaly detection
│   ├── patchtst_detector.py   # PatchTST forecaster + z-score fallback
│   └── analyzer.py            # SignalAnalyzer — derives signals, annotates graph
│
├── rca/                       # Root cause analysis
│   ├── context_builder.py     # ContextWindow assembly
│   └── analyzer.py            # RCAAnalyzer + RCAReport
│
├── llm/
│   └── ollama_client.py       # Ollama /api/generate + streaming
│
├── workflow/                  # LangGraph stateful workflow
│   ├── state.py               # RCAState (serialisable) + WorkflowConfig (injected)
│   ├── nodes.py               # Node functions + confidence_router + human_router
│   └── graph.py               # build_graph() — StateGraph with human interrupt edge
│
├── k8s/                       # Kubernetes manifests
│   ├── rbac.yaml              # ServiceAccount + ClusterRole (read-only)
│   ├── ollama.yaml            # Ollama Deployment + Service + PVC
│   └── kubewhisperer.yaml     # CronJob + ConfigMap + PVC
│
├── scripts/
│   └── init-k3s.sh            # One-shot K3s bootstrap
│
├── tests/
│   ├── conftest.py            # synthetic_graph fixture (degraded production scenario)
│   ├── unit/                  # 200+ unit tests (no cluster required)
│   └── integration/           # Pipeline tests with mock LLM
│
├── docs/
│   ├── architecture.md        # Component deep-dive
│   ├── configuration.md       # All env variables
│   └── deployment.md          # K3s, existing cluster, local dev
│
├── Dockerfile                 # Multi-stage Python 3.11 image
├── .env.example               # Config template
└── requirements.txt
```

---

## RBAC

KubeWhisperer needs **read-only** cluster access. The `ClusterRole` in `k8s/rbac.yaml` grants
`get`, `list`, `watch` on all core resource types, `apps`, `batch`, `networking.k8s.io`,
`autoscaling`, and non-resource URLs for API discovery.
No `create`, `update`, `patch`, or `delete` permissions are granted.

---

## Roadmap

- [x] **LangGraph workflow** (`workflow/`) — stateful RCA graph with conditional retry on LOW confidence, human-in-the-loop interrupt before remediation, and resume via `Command(resume="approve"|"reject")`
- [ ] Prometheus / Grafana alert correlation
- [ ] Multi-cluster support
- [ ] Slack / PagerDuty incident enrichment via webhook
- [ ] Web UI (read-only dashboard)
- [ ] RBAC-aware context scoping (per-namespace analysis)

---

## License

MIT
