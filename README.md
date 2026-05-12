# KubeWhisperer

> Automated Root Cause Analysis for Kubernetes — multi-path LLM reasoning, fully local, no data leaves your infrastructure.

[![Tests](https://img.shields.io/badge/tests-896%20passed-brightgreen)](#tests)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

KubeWhisperer combines a typed Kubernetes ontology, a GitOps drift engine, real-time observability ingestion (Prometheus, OTel/Tempo/Jaeger, Loki, metrics-server), a multi-path LLM reasoning workflow (LangGraph), a local FAISS vector store, and an enterprise knowledge base — all running locally with Mistral via Ollama.

---

## How it works

```
K8s API + Helm + Helmfile
        │
        ▼
  OntologyGraph  ──────────────────────────────────────────────────────┐
  (typed entities:                                                     │
   Pod, Deployment, Service,                                           │
   HelmRelease, HelmChart,                                             │
   ConfigMap, Secret, PVC,                                             │
   PrometheusAlert, OtelTrace, LokiLog, …)                             │
        │                                                              │
        ├──► MetricsServerCollector                                    │
        │    └── metrics.k8s.io/v1beta1 ──► metrics.cpu_m/memory_mi   │
        │                                                              │
        ├──► PrometheusCollector                                       │
        │    ├── firing alerts → PrometheusAlert nodes (HAS_ALERT)     │
        │    └── alert.* annotations on correlated entities            │
        │                                                              │
        ├──► OtelCollector (Tempo / Jaeger)                            │
        │    └── error traces → OtelTrace nodes (HAS_TRACE)            │
        │        otel.trace.* annotations + LokiSource (HAS_LOG)       │
        │                                                              │
        ├──► HelmDriftDetector ──► drift.* annotations                 │
        │    (declared ≠ observed)                                      │
        │                                                              │
        ├──► AnchorEngine                                              │
        │    ├── K8s schema defaults (valid values, descriptions)       │
        │    └── helm template output (declared chart values)          │
        │        ──► anchor.* annotations + helm fix suggestions       │
        │                                                              │
        ├──► GitOpsCollector (optional)                                │
        │    ├── git clone / GitHub API                                │
        │    ├── helm template → rendered manifests                    │
        │    └── ManifestDiffer ──► gitops.* annotations               │
        │                                                              │
        ├──► SignalAnalyzer (PatchTST)                                 │
        │    ├── real CPU/memory from metrics-server                   │
        │    ├── real time series from Prometheus (3 horizons)         │
        │    ├── restart_count / ready_ratio / event_count anomalies   │
        │    └── ──► signal.* annotations                              │
        │                                                              │
        └──► FAISSStore (all-MiniLM-L6-v2)  ◄── Enterprise Knowledge ──┘
             ├── K8s entities (semantic search)
             ├── K8s docs (versioned, fetched per cluster version)
             └── Enterprise docs (runbooks, SOPs, Confluence, wikis)
                        │
                  ContextWindow
                  ├── [CRITICAL] unhealthy seeds
                  ├── [CRITICAL] Helm drift (declared ≠ observed)
                  ├── [CRITICAL] firing Prometheus alerts
                  ├── [TRACES]   OTel error spans (cap 20)
                  ├── [LOGS]     Loki error/warn lines (cap 20)
                  ├── [ANCHOR FIX] helm upgrade commands (restore declared values)
                  ├── [SIGNALS]   PatchTST anomalies
                  ├── [WARNINGS]  K8s events
                  ├── [ANCHORS]   schema defaults + valid values
                  ├── [Helm]      releases + charts
                  └── [Related]   BFS neighbours + FAISS hits (Jaccard dedup + TF-IDF rank)
                        │
              ┌─────────▼─────────────────────────────────────────────┐
              │          LangGraph multi-path reasoning workflow       │
              │                                                         │
              │  hypothesize ──► LLM generates H1 / H2 / H3           │
              │       │          from cluster snapshot                  │
              │       ▼                                                 │
              │   analyze ──► LLM investigates current hypothesis      │
              │       │       with full ContextWindow                  │
              │       │                                                 │
              │  confidence_router                                      │
              │   ├── HIGH / MEDIUM ──────────────────────────────┐    │
              │   ├── LOW + retries left ──► increment_retry ──►  │    │
              │   │                          analyze again         │    │
              │   └── LOW + all retries used                       │    │
              │        └── candidates left? ──► archive_path ──►  │    │
              │                                 next hypothesis    │    │
              │             all paths exhausted ──► select_best ──┘    │
              │                                    (restore highest     │
              │                                     confidence path)    │
              │                                         │               │
              └─────────────────────────────────────────▼───────────────┘
                                                    dry_run
                                                    each remediation cmd:
                                                    ├── helm diff / values diff
                                                    └── kubectl --dry-run=server
                                                         │
                                             human_review [INTERRUPT]
                                             operator sees:
                                               - reasoning journey (all paths)
                                               - root cause + causal chain
                                               - dry-run output per command
                                               - confidence + paths explored
                                                     │
                                           approve ──┤── reject
                                                     │
                                              RCAReport
                                              ├── summary
                                              ├── affected resources
                                              ├── root cause
                                              ├── causal chain
                                              ├── remediation (kubectl / helm)
                                              └── confidence
```

---

## Streamlit UI

```
streamlit run ui/app.py
```

Two-tab layout:

### Tab 1 — Root Cause Analysis

- **Sidebar**: kube context selector, namespace, optional collectors (Metrics server, GitOps drift, Prometheus, OTel/Loki)
- **Pipeline bar**: 8-step progress — K8s+Helm · Metrics · Prometheus · OTel · GitOps · Anchors · FAISS · PatchTST
- **Context banner**: cluster, namespace, entity count, K8s version, elapsed time
- **Helm drift table**: declared vs observed, colour-coded by severity
- **Reasoning journey**: collapsible path history (exhausted paths) + current best path
- **Root cause + remediation**: causal chain + executable `kubectl`/`helm` commands
- **Human review gate**: Approve / Reject with `Command(resume=…)` LangGraph handoff

### Tab 2 — Knowledge Base

| Sub-tab | Content |
|---|---|
| **Ontology** | Filterable entity browser — kind / namespace / name / annotation count |
| **Anchors** | Helm fix suggestions (manifest anchors on unhealthy pods) + full anchor records |
| **K8s Docs** | Version-adaptive links (e.g. `v1-31.docs.kubernetes.io`) + Fetch & Index 16 key pages |
| **Enterprise Docs** | Manual text / file upload / URL fetch (Confluence API auto-detected) + tag filter + re-index |

---

## Key properties

| Property | Detail |
|---|---|
| **Data sovereignty** | All inference runs locally — cluster data never leaves your network |
| **Air-gapped** | Works without internet once models and dependencies are pulled |
| **Ontology-aware** | Typed entities (Pod, Deployment, HelmRelease, OtelTrace, LokiLog, …) with 16 directed relationship edge types |
| **Helm + Helmfile** | Correlates declared chart values with live runtime state; detects drift at field level |
| **GitOps diff** | Clones chart repo (or uses GitHub API), runs `helm template`, diffs rendered vs observed |
| **AnchorEngine** | Extracts declared values from `helm template` output; maps to `helm upgrade --set` fix commands |
| **Dynamic discovery** | Queries `/apis` to index CRDs and operator resources automatically |
| **Multi-version K8s** | Detects server version; drives API choices for 1.16 → 1.31+ and K3s |
| **Prometheus alerts** | Correlates firing alerts with K8s entities via label matching; `alert.*` annotations in context |
| **OTel traces** | Fetches error spans from Tempo or Jaeger; wires `HAS_TRACE` edges and `[TRACES]` context section |
| **Loki logs** | Queries pod logs via LogQL; extracts log level + OTel trace IDs; `[LOGS]` context section |
| **Metrics server** | Live CPU/memory from `metrics.k8s.io/v1beta1` anchors PatchTST signals on real resource usage |
| **PatchTST signals** | Forecasting-based anomaly detection on real Prometheus time series at 3 horizons (1h/24h/7d) |
| **Trigram TF-IDF** | K8s-aware tokenisation preserves `phase=Failed`, `apps/v1`, `v1.31.5+k3s1` |
| **Multi-path reasoning** | LLM generates H1/H2/H3 hypotheses; explores each, archives dead ends, selects best path |
| **Enterprise knowledge** | DocStore + DocIndexer — runbooks, SOPs, Confluence, wikis indexed into FAISS for RAG |
| **Versioned K8s docs** | Fetches and indexes official K8s docs at the detected cluster version |

---

## Quick start

**Prerequisites:** Python 3.11+, a Kubernetes cluster reachable via kubeconfig, Ollama with `mistral` pulled.

```bash
# Clone and set up
git clone https://github.com/your-org/kubewhisperer.git
cd kubewhisperer
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env: KUBECONFIG, OLLAMA_URL, KUBE_NAMESPACES, etc.

# Pull the local model (one-time)
ollama pull mistral

# Launch the UI
streamlit run ui/app.py
```

---

## Demo

A local demo deploys 5 incident scenarios on a k3d cluster — no external dependencies.

```bash
# Deploy incident scenarios (k3d must be running)
bash demo/setup.sh

# Then open the UI and run analysis on namespace: kubewhisperer-demo
streamlit run ui/app.py
```

Incident scenarios deployed by the demo:

| Service | Failure | Root cause |
|---|---|---|
| `payment-service` | CrashLoopBackOff | Missing `db-primary` service — DB connection refused |
| `notification-service` | CreateContainerConfigError | Missing `notification-config` ConfigMap |
| `ml-inference` | ImagePullBackOff | Image drift patch pointing to private registry |
| `analytics-worker` | OOMKilled / Pending | Memory limit drift: 512Mi → 50Mi |
| `gpu-worker` | Pending | GPU node affinity unsatisfiable |
| `api-gateway` | Running ✓ | Healthy baseline |

---

## Deploy on K3s (fully local stack)

```bash
# Build and load the image
docker build -t ghcr.io/your-org/kubewhisperer:latest .
docker save ghcr.io/your-org/kubewhisperer:latest | sudo k3s ctr images import -

# Bootstrap K3s + Ollama + KubeWhisperer CronJob
sudo bash scripts/init-k3s.sh --image ghcr.io/your-org/kubewhisperer:latest
```

For an existing cluster:

```bash
kubectl create namespace kubewhisperer
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/ollama.yaml
kubectl apply -f k8s/kubewhisperer.yaml
```

---

## Tests

```bash
pytest                          # 896 tests (unit + integration)
pytest tests/unit/              # unit only (no cluster required)
pytest tests/integration/       # pipeline tests with mock LLM
pytest --cov=. --cov-report=term-missing
```

---

## Project layout

```
kubewhisperer/
├── config.py                   # .env loader + typed config constants
├── main.py                     # CLI entry point
│
├── ontology/                   # K8s knowledge model
│   ├── entities.py             # Typed dataclasses: Pod, Deployment, HelmRelease, …
│   ├── graph.py                # OntologyGraph — nodes, edges, BFS
│   ├── relationships.py        # 14 edge types
│   ├── version.py              # KubeVersion + feature flags
│   ├── discovery.py            # Dynamic API server discovery (/apis)
│   └── dynamic_entity.py       # GenericEntity for CRDs
│
├── ingestion/                  # Cluster + Helm data collection
│   ├── k8s_collector.py        # Kubernetes API collector (version-aware)
│   ├── helm_collector.py       # helm get values / chart parsing
│   ├── helm_drift.py           # Helm declared vs K8s observed
│   ├── helmfile_collector.py   # Helmfile YAML parsing
│   ├── chart_parser.py         # Chart.yaml, umbrella deps, value hierarchy
│   ├── git_provider.py         # LocalGitProvider + GithubProvider (REST API)
│   ├── manifest_renderer.py    # helm template → []dict
│   ├── manifest_differ.py      # rendered vs observed drift detection
│   ├── gitops_collector.py     # GitopsCollector orchestrator
│   ├── anchor_engine.py        # Declared-value anchors (schema + rendered manifests)
│   ├── k8s_schema.py           # Embedded K8s API field metadata
│   ├── metrics_server_collector.py  # Live CPU/memory from metrics.k8s.io
│   ├── prometheus_collector.py     # Firing alert ingestion + entity correlation
│   ├── otel_backend.py             # OtelBackend ABC + TempoBackend + JaegerBackend
│   ├── otel_collector.py           # Error trace fetching → HAS_TRACE edges
│   └── loki_source.py              # Pod log fetching via LogQL → HAS_LOG edges
│
├── dedup/                      # Context deduplication pipeline
│   ├── bfs.py                  # Graph BFS from unhealthy seeds
│   ├── jaccard.py              # Token-level dedup
│   └── tfidf.py                # TF-IDF trigram ranking
│
├── vectorstore/                # Semantic search
│   ├── embedder.py             # sentence-transformers + L2 normalisation
│   └── store.py                # FAISSStore (IndexFlatIP, save/load)
│
├── knowledge/                  # Enterprise knowledge base
│   ├── doc_store.py            # DocStore — JSON-backed persistence (./data/docs/)
│   ├── doc_indexer.py          # DocIndexer — chunking + FAISS indexing
│   └── __init__.py
│
├── signals/                    # Time-series anomaly detection
│   ├── patchtst_detector.py    # PatchTST forecaster + z-score fallback
│   ├── prometheus_source.py    # PrometheusMetricSource — 3-horizon real time series
│   └── analyzer.py             # SignalAnalyzer — derives signals, annotates graph
│
├── rca/                        # Root cause analysis
│   ├── context_builder.py      # ContextWindow assembly + anchor_fix_hints()
│   └── analyzer.py             # RCAAnalyzer + RCAReport
│
├── llm/
│   └── ollama_client.py        # Ollama /api/generate + streaming
│
├── workflow/                   # LangGraph stateful multi-path workflow
│   ├── state.py                # RCAState — candidate_paths, reasoning_history, current_hypothesis
│   ├── nodes.py                # hypothesize, analyze, archive_path, select_best, dry_run, human_review, …
│   └── graph.py                # build_graph() — StateGraph topology
│
├── ui/
│   └── app.py                  # Streamlit UI — RCA tab + Knowledge Base tab
│
├── demo/                       # Local demo — 5 incident scenarios on k3d
│   ├── setup.sh                # Deploy charts + drift patches + GitOps repo
│   ├── cleanup.sh              # Tear down demo namespace
│   ├── run_rca.py              # CLI demo runner
│   ├── charts/                 # payment-service, analytics-worker, ml-inference, …
│   └── manifests/              # gpu-worker (Pending), api-gateway (healthy)
│
├── k8s/                        # Kubernetes manifests for production deployment
│   ├── rbac.yaml               # ServiceAccount + ClusterRole (read-only)
│   ├── ollama.yaml             # Ollama Deployment + Service + PVC
│   └── kubewhisperer.yaml      # CronJob + ConfigMap + PVC
│
├── tests/
│   ├── conftest.py             # synthetic_graph fixture (degraded production scenario)
│   ├── unit/                   # 896 tests (no cluster required)
│   └── integration/            # Pipeline tests with mock LLM
│
├── Dockerfile                  # Multi-stage Python 3.11 image
├── .env.example                # Config template
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

### Done

- [x] **LangGraph multi-path workflow** — hypothesize (LLM-generated H1/H2/H3) → analyze → retry / archive_path → select_best → dry_run → human_review
- [x] **AnchorEngine** — manifest + schema anchors; `anchor_fix_hints()` generates `helm upgrade --set` commands for each unhealthy entity
- [x] **GitOps diff** — `helm template` rendered manifests vs live cluster; `LocalGitProvider` + `GithubProvider`
- [x] **Enterprise Knowledge Base** — DocStore + DocIndexer; runbooks, SOPs, Confluence (auto-detected), wikis indexed into FAISS
- [x] **Versioned K8s docs** — fetch & index official K8s docs at detected cluster version (e.g. `v1-31.docs.kubernetes.io`)
- [x] **Streamlit UI** — pipeline bar, reasoning journey, drift table, anchor fix panel, KB browser
- [x] **PatchTST signals** — multi-horizon anomaly detection on real Prometheus time series (1h/24h/7d) + z-score fallback
- [x] **Prometheus alert correlation** — firing alerts ingested via `PrometheusCollector`; label-matched to K8s entities; `[CRITICAL]` context section
- [x] **OTel traces** — `OtelCollector` (Tempo + Jaeger backends) fetches error spans; `HAS_TRACE` edges; `[TRACES]` context section
- [x] **Loki logs** — `LokiSource` queries pod logs via LogQL; extracts log level + trace IDs; `[LOGS]` context section
- [x] **Metrics server** — `MetricsServerCollector` annotates pods with live CPU/memory; seeds PatchTST with real resource values
- [x] **Demo scenarios** — 5 incident types deployed on k3d (CrashLoop, ConfigError, ImagePull, OOMKill, Pending)

### Next

- [ ] **Weaviate vector store** — replace FAISS with hybrid BM25+vector, persistent index

  | | FAISS (current) | Weaviate (planned) |
  |---|---|---|
  | Persistence | manual `.faiss` save/load | built-in |
  | Search | pure vector (cosine) | BM25 + vector (hybrid) |
  | Air-gap | yes | local container |
  | Best for | single cluster, ephemeral | large clusters, persistent |

- [ ] **Multi-cluster support** — analyse multiple contexts in one session
- [ ] **Slack / PagerDuty enrichment** — push RCA summary via webhook
- [ ] **RBAC-aware scoping** — per-namespace analysis with service-account impersonation

---

## License

MIT
