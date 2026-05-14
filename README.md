# KubeWhisperer

> Automated Root Cause Analysis for Kubernetes — multi-path LLM reasoning, fully local, no data leaves your infrastructure.

[![Tests](https://img.shields.io/badge/tests-1100%2B%20passed-brightgreen)](#tests)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)

KubeWhisperer combines a typed Kubernetes ontology, a GitOps drift engine, real-time observability ingestion (Prometheus, OTel/Tempo/Jaeger, Loki, metrics-server), a multi-path LLM reasoning workflow (LangGraph), a hybrid BM25+FAISS retrieval pipeline (RRF), an anchor-driven remediation engine, and an enterprise knowledge base — all running locally with Mistral via Ollama.

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
   PrometheusAlert, OtelTrace, LokiLog,                                │
   PolicyViolation, MutatingWebhook, …)                                │
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
        │    (declared ≠ observed)                                     │
        │                                                              │
        ├──► AnchorEngine                                              │
        │    ├── K8s schema defaults (valid values, descriptions)      │
        │    └── helm template output (declared chart values)          │
        │        ──► anchor.* annotations                              │
        │            anchor_fix_hints() → kubectl/helm fix commands    │
        │                                                              │
        ├──► DeploymentReadinessDetector                               │
        │    ├── scan pod spec: envFrom · env.valueFrom · volumes      │
        │    │   imagePullSecrets · serviceAccountName                 │
        │    ├── cross-check secrets / configmaps / pvcs / SAs         │
        │    ├── RBAC: SA exists but no (Cluster)RoleBinding           │
        │    ├── NetworkPolicy: egress: [] → all traffic blocked        │
        │    └── missing.* / netpol.* annotations → kubectl create cmds│
        │                                                              │
        ├──► PolicyCollector (OPA / Kyverno)                           │
        │    ├── PolicyReport / ClusterPolicyReport (wgpolicyk8s.io)   │
        │    ├── MutatingWebhookConfiguration                          │
        │    └── PolicyViolation nodes (HAS_POLICY_VIOLATION edges)    │
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
             ├── Dense search (cosine, FAISS IndexFlatIP)
             ├── Sparse search (BM25 — K8s-aware tokeniser)
             └── Hybrid fusion: Reciprocal Rank Fusion (RRF)
                 ├── K8s entities                    doc_source=cluster   ×1.0
                 ├── K8s docs (versioned)             doc_source=official  ×1.0
                 ├── Past incidents                   doc_source=example   ×1.2
                 ├── Enterprise docs (Confluence, wikis) doc_source=enterprise ×1.5
                 ├── Runbooks / SOPs                  doc_source=runbook   ×1.8
                 └── Helm / Helmfile charts           doc_source=helm      ×1.5
                        │
                  ContextWindow
                  ├── [CRITICAL] Policy violations (OPA / Kyverno)
                  ├── [CRITICAL] unhealthy seeds
                  ├── [CRITICAL] Helm drift (declared ≠ observed)
                  ├── [CRITICAL] firing Prometheus alerts
                  ├── [ANCHOR FIX] kubectl create / helm upgrade commands (missing deps + drift)
                  ├── [TRACES]   OTel error spans (cap 20)
                  ├── [LOGS]     Loki error/warn lines (cap 20)
                  ├── [SIMILAR]  resolved past incidents (FAISS examples)
                  ├── [WARNINGS] K8s events
                  ├── [ANCHORS]  schema defaults + valid values (pivot: declared→observed→fix)
                  ├── [Helm]     releases + charts
                  └── [Related]  BFS neighbours + RRF hits (Jaccard dedup + TF-IDF rank)
                        │
              ┌─────────▼─────────────────────────────────────────────┐
              │          LangGraph multi-path reasoning workflow      │
              │                                                       │
              │  hypothesize ──► LLM generates H1 / H2 / H3           │
              │       │          from cluster snapshot                │
              │       ▼                                               │
              │   analyze ──► LLM investigates current hypothesis     │
              │       │       with full ContextWindow                 │
              │       │                                               │
              │  confidence_router                                    │
              │   ├── HIGH / MEDIUM ──────────────────────────────┐   │
              │   ├── LOW + retries left ──► increment_retry ──►  │   │
              │   │                          analyze again        │   │
              │   └── LOW + all retries used                      │   │
              │        └── candidates left? ──► archive_path ──►  │   │
              │                                 next hypothesis   │   │
              │             all paths exhausted ──► select_best ──┘   │
              │                                    (restore highest   │
              │                                     confidence path)  │
              │                                         │             │
              └─────────────────────────────────────────▼─────────────┘
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

### Tab 1 — Root Cause Analysis

- **Sidebar**: kube context selector, namespace, optional collectors (Metrics server, GitOps drift, Prometheus, OTel/Loki)
- **Pipeline bar**: 8-step progress — K8s+Helm · Metrics · Prometheus · OTel · GitOps · Anchors · FAISS · PatchTST
- **Context banner**: cluster, namespace, entity count, K8s version, elapsed time
- **Retrieval expander**: BM25+FAISS→RRF stats — dense hits / sparse hits / fused hits / top RRF score
- **Helm drift table**: declared vs observed, colour-coded by severity
- **Reasoning journey**: collapsible path history (exhausted paths) + current best path
- **Root cause + remediation**: causal chain + executable `kubectl`/`helm` commands
- **Human review gate**: Approve / Reject with `Command(resume=…)` LangGraph handoff

### Tab 2 — Knowledge Base

| Sub-tab | Content |
|---|---|
| **Ontology** | Filterable entity browser — kind / namespace / name / annotation count |
| **Anchors** | Helm fix suggestions (manifest anchors on unhealthy pods) + full anchor records |
| **K8s Docs** | Version-adaptive links + Fetch & Index 16 key pages at detected cluster version |
| **Enterprise Docs** | Manual text / file upload / URL fetch (Confluence auto-detected) + tag filter |
| **Helm / Helmfile** | Upload `values.yaml`, `helmfile.yaml`, or `.tgz` chart archives — indexed as helm source documents |

### Tab 3 — Dashboard

- Ingestion pipeline step table (last run status + stats)
- Knowledge base metrics: enterprise docs / K8s docs / references / total content
- Tag breakdown bar chart
- Source weight configuration

### Tab 4 — Integration Tests

Cluster-free pipeline exploration — no Ollama required for **🔬 Pipeline trace** mode.

**Dropdown**: all registered test cases from:
- `cases/NNN_*/` — synthetic JSON fixtures (shown when `tests/unit/test_hybrid_pipeline_NNN.py` exists)
- `cases/helm_cases/h*/` — Helm chart cases (native YAML + values + observed)
- `tests/integration/cases/h*/` — **native K8s cases** (kube YAML + helm + helmfile + policy reports)

**Modes**:

| Mode | Requires Ollama | Description |
|---|---|---|
| 🔬 Pipeline trace | No | 10-step pre-LLM pipeline visualization (default) |
| Auto (full BFS) | Yes | Full dialogue simulation with LLM |
| Manual (step-by-step) | Yes | Interactive turn-by-turn simulation |

**Pipeline trace steps** (auto-runs on case selection, cached per case):

| Step | What it shows |
|---|---|
| 1 | BM25 tokenizer — query tokens |
| 2 | FAISS dense hits (cosine similarity) |
| 3 | BM25 sparse hits (keyword) |
| 4 | RRF fusion — dense + sparse metrics |
| 5 | Seeds — unhealthy resources + Warning events |
| 6 | **Anchor pivot table** — declared → observed → status → fix command |
| 7 | Jaccard deduplication — candidates / kept / diversity ratio |
| 8 | TF-IDF ranked context chunks |
| 9 | Pre-LLM confidence score breakdown |
| 10 | **Proposed changes** — values.yaml diff · `helm upgrade` commands · OPA/Kyverno fixes |
| Bonus | RemediationEngine rule-based hypotheses |
| Bonus | LLM prompt dry-run (full prompt preview) |

---

## Anchor pivot — declared → observed → fix

Anchors bridge chart intent to live cluster state to concrete remediation commands. The pivot covers **all deployment blockers**, not just value drift:

```
values.yaml declares:   resources.limits.memory = 512Mi
Observed (deployed):    resources.limits.memory = 128Mi        ← DRIFT
Fix:                    helm upgrade api -n production --set resources.limits.memory=512Mi

Pod references:         secret/payment-db-secret (envFrom)
Observed:               secret not found in namespace
Fix:                    kubectl create secret generic payment-db-secret -n production --from-literal=…

NetworkPolicy applied:  order-service-restrict-egress  egress: []
Effect:                 ALL outbound blocked (DNS 53, PostgreSQL 5432, Redis 6379)
Fix:                    kubectl edit networkpolicy order-service-restrict-egress -n production

ServiceAccount:         audit-exporter-sa  (exists, no RoleBinding)
Fix:                    kubectl create clusterrolebinding audit-exporter-rb --clusterrole=view --serviceaccount=production:audit-exporter-sa
```

The **Step 6** anchor table in the pipeline trace renders declared vs observed for every field:

| resource | field | declared | observed | source | status | fix |
|---|---|---|---|---|---|---|
| api | `resources.limits.memory` | `512Mi` | `128Mi` | values.yaml | 🔴 DRIFT | `helm upgrade api --set resources.limits.memory=512Mi` |
| api | `image.tag` | `v3.2.0` | `v3.2.0` | values.yaml | ✅ OK | — |

**Step 10** proposes all changes in priority order:
1. 🔴 **Missing deployment dependencies** — `kubectl create secret/configmap/serviceaccount/pvc`
2. 🌐 **NetworkPolicy blockers** — `kubectl edit networkpolicy` + egress rule template
3. 🔒 **OPA / Kyverno policy fixes** — policy-specific remediation hints
4. 🔀 **Helm drift** — `helm upgrade --set` to restore declared values
5. 📄 **Declared values.yaml** (collapsed reference)

---

## Integration test cases — native Kubernetes format

Test cases in `tests/integration/cases/` use real Kubernetes artifact formats instead of custom JSON:

```
tests/integration/cases/
├── h001_crashloopbackoff/
│   ├── kube/
│   │   ├── pod.yaml          ← kubectl get pod -o yaml
│   │   └── events.yaml       ← kubectl get events -o yaml (EventList)
│   ├── helm/
│   │   ├── values.yaml       ← declared chart values
│   │   └── release.json      ← helm get values -o json (deployed state)
│   └── expect.json           ← test expectations
├── h002_imagepullbackoff/    ← image tag drift v2.0.5 → v2.1.0-private, 401 Unauthorized
├── h003_oomkilled/           ← memory limit drift 512Mi declared → 128Mi deployed, OOMKilled
├── h004_missing_configmap/   ← CreateContainerConfigError — 3 missing resources (ConfigMap + 2 Secrets)
├── h005_rbac_forbidden/      ← SA exists, no ClusterRoleBinding → 403 Forbidden on all API calls
│   └── kube/rbac/            ← optional subdirectory for RBAC resources
└── h006_networkpolicy_blocked/ ← egress: [] blocks DNS + PostgreSQL + Redis; pod Running but not Ready
```

The `case_loader.py` reads all formats (YAML/JSON), runs `HelmDriftDetector` + `AnchorEngine` + `_detect_missing_deps()`, and produces a full `OntologyGraph` — the same pipeline used against a real cluster. It recurses into subdirectories under `kube/` (e.g. `kube/rbac/`) and collects all resource kinds including secrets, configmaps, serviceaccounts, networkpolicies, pvcs, and RBAC objects.

Add a new case: create `tests/integration/cases/hNNN_name/` with the YAML artifacts. It appears automatically in the UI dropdown.

---

## Key properties

| Property | Detail |
|---|---|
| **Data sovereignty** | All inference runs locally — cluster data never leaves your network |
| **Air-gapped** | Works without internet once models and dependencies are pulled |
| **Ontology-aware** | Typed entities (Pod, Deployment, HelmRelease, OtelTrace, LokiLog, PolicyViolation, …) with 16 directed relationship edge types |
| **Helm + Helmfile** | Correlates declared chart values with live runtime state; detects drift at field level |
| **GitOps diff** | Clones chart repo (or uses GitHub API), runs `helm template`, diffs rendered vs observed |
| **AnchorEngine** | Extracts declared values from `helm template` output; maps to `helm upgrade --set` fix commands; rendered as pivot table in UI |
| **Deployment readiness** | Scans pod specs for all resource references; detects missing secrets, configmaps, PVCs, imagePullSecrets, serviceaccounts; flags RBAC gaps and NetworkPolicy egress blocks; `anchor_fix_hints()` generates concrete `kubectl create/edit` commands |
| **BM25 + FAISS hybrid** | Dense cosine (FAISS) + sparse keyword (BM25) fused with Reciprocal Rank Fusion; `retrieval_stats` exposed in UI |
| **Dynamic discovery** | Queries `/apis` to index CRDs and operator resources automatically |
| **Multi-version K8s** | Detects server version; drives API choices for 1.16 → 1.31+ and K3s |
| **Prometheus alerts** | Correlates firing alerts with K8s entities via label matching; `alert.*` annotations in context |
| **OTel traces** | Fetches error spans from Tempo or Jaeger; wires `HAS_TRACE` edges and `[TRACES]` context section |
| **Loki logs** | Queries pod logs via LogQL; extracts log level + OTel trace IDs; `[LOGS]` context section |
| **Metrics server** | Live CPU/memory from `metrics.k8s.io/v1beta1` anchors PatchTST signals on real resource usage |
| **PatchTST signals** | Forecasting-based anomaly detection on real Prometheus time series at 3 horizons (1h/24h/7d) |
| **Trigram TF-IDF** | K8s-aware tokenisation preserves `phase=Failed`, `apps/v1`, `v1.31.5+k3s1` |
| **Multi-path reasoning** | LLM generates H1/H2/H3 hypotheses; explores each, archives dead ends, selects best path |
| **Enterprise knowledge** | DocStore + DocIndexer — runbooks, SOPs, Confluence, wikis, Helm charts indexed into FAISS for RAG |
| **Versioned K8s docs** | Fetches and indexes official K8s docs at the detected cluster version |
| **Source weights** | Per-source score multipliers applied before TF-IDF ranking — enterprise ×1.5, runbook ×1.8, configurable via `SOURCE_WEIGHT_*` env vars |
| **OPA / Kyverno** | PolicyReport / ClusterPolicyReport ingested; violations wired as `HAS_POLICY_VIOLATION` edges; confidence boost; fix hints in Step 10 |
| **Pre-LLM pipeline trace** | Full 10-step pre-LLM pipeline visualization in UI — no Ollama required; auto-runs on case selection |
| **Native test cases** | `tests/integration/cases/` — real K8s YAML artifacts (pod, deployment, events, values.yaml, helmfile, PolicyReport) |

---

## Quick start

**Prerequisites:** Python 3.11+, a Kubernetes cluster reachable via kubeconfig, Ollama with `mistral` pulled.

```bash
git clone https://github.com/your-org/kubewhisperer.git
cd kubewhisperer
pip install -r requirements.txt

cp .env.example .env
# Edit .env: KUBECONFIG, OLLAMA_URL, KUBE_NAMESPACES, etc.

ollama pull mistral
streamlit run ui/app.py
```

### Try without a cluster

The **Integration Tests** tab runs entirely offline — no cluster, no Ollama needed:

1. Open the UI: `streamlit run ui/app.py`
2. Go to **🧪 Integration Tests**
3. Select any `h00N_*` case from the dropdown
4. Mode defaults to **🔬 Pipeline trace** — pipeline runs automatically
5. Explore all 10 steps: tokenizer → retrieval → anchors → drift → confidence → proposed fixes

---

## Demo

A local demo deploys incident scenarios on a k3d cluster — no external dependencies.

```bash
bash demo/setup.sh
streamlit run ui/app.py
# Analyse namespace: kubewhisperer-demo
```

| Service | Failure | Root cause |
|---|---|---|
| `payment-service` | CrashLoopBackOff | Missing `db-primary` service — DB connection refused |
| `notification-service` | CreateContainerConfigError | Missing `notification-config` ConfigMap |
| `ml-inference` | ImagePullBackOff | Image tag drift pointing to private registry |
| `analytics-worker` | OOMKilled / Pending | Memory limit drift: 512Mi → 50Mi |
| `gpu-worker` | Pending | GPU node affinity unsatisfiable |
| `api-gateway` | Running ✓ | Healthy baseline |

---

## Tests

```bash
pytest                               # all tests (unit + cases + integration)
pytest tests/unit/                   # unit only — no cluster, no LLM
pytest tests/cases/                  # offline JSON fixture regression (20 scenarios)
pytest tests/unit/test_hybrid_pipeline_001.py  # h001 CrashLoopBackOff pipeline
pytest tests/unit/test_hybrid_pipeline_002.py  # h002 ImagePullBackOff pipeline
pytest tests/integration/            # pipeline tests with mock LLM
pytest --cov=. --cov-report=term-missing
```

The `test_hybrid_pipeline_NNN.py` files are also the **registration mechanism** for the UI dropdown — creating one registers the corresponding case in the Integration Tests tab.

---

## Project layout

```
kubewhisperer/
├── config.py                   # .env loader + typed config constants
├── main.py                     # CLI entry point
│
├── ontology/                   # K8s knowledge model
│   ├── entities.py             # Typed dataclasses: Pod, Deployment, HelmRelease, PolicyViolation, …
│   ├── graph.py                # OntologyGraph — nodes, edges, BFS
│   ├── relationships.py        # 16 edge types
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
│   ├── anchor_engine.py        # Declared-value anchors (schema + rendered manifests)
│   ├── k8s_schema.py           # Embedded K8s API field metadata
│   ├── policy_collector.py     # OPA/Kyverno PolicyReport + MutatingWebhook → HAS_POLICY_VIOLATION
│   ├── metrics_server_collector.py
│   ├── prometheus_collector.py
│   ├── otel_backend.py
│   ├── otel_collector.py
│   ├── loki_source.py
│   ├── git_provider.py
│   ├── manifest_renderer.py
│   ├── manifest_differ.py
│   └── gitops_collector.py
│
├── dedup/                      # Context deduplication pipeline
│   ├── bfs.py                  # Graph BFS from unhealthy seeds
│   ├── jaccard.py              # Token-level Jaccard dedup
│   └── tfidf.py                # TF-IDF trigram ranking
│
├── vectorstore/                # Hybrid retrieval
│   ├── embedder.py             # sentence-transformers + L2 normalisation
│   ├── bm25_retriever.py       # BM25 sparse retriever (K8s-aware tokeniser)
│   ├── rrf.py                  # Reciprocal Rank Fusion
│   └── store.py                # FAISSStore — dense + BM25 + RRF hybrid search
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
│   ├── confidence.py           # compute_confidence() — pre-LLM context quality score
│   ├── context_builder.py      # ContextWindow assembly + anchor_fix_hints()
│   ├── remediation_engine.py   # Rule-based weighted hypotheses (LOW-confidence fallback)
│   └── analyzer.py             # RCAAnalyzer + RCAReport
│
├── llm/
│   └── ollama_client.py        # Ollama /api/generate + streaming
│
├── workflow/                   # LangGraph stateful multi-path workflow
│   ├── state.py
│   ├── nodes.py
│   └── graph.py
│
├── ui/
│   └── app.py                  # Streamlit UI — 4 tabs (RCA / KB / Dashboard / Integration Tests)
│
├── cases/                      # JSON test fixtures (20 failure scenarios)
│   ├── 001_crashloopbackoff/   # input.json · expect.json · helm/values.yaml
│   ├── 002_imagepullbackoff/   # input.json · expect.json · helm/values.yaml
│   ├── 003_oomkilled/ … 020_ingress_backend_not_found/
│   └── helm_cases/             # Native Helm format (values.yaml + observed/*.json)
│       └── h001_oom_memory_limit/
│
├── tests/
│   ├── conftest.py
│   ├── unit/                   # Unit tests — no cluster, no LLM
│   │   ├── test_hybrid_pipeline_001.py   # h001 CrashLoopBackOff — 9 test classes
│   │   ├── test_hybrid_pipeline_002.py   # h002 ImagePullBackOff — 9 test classes
│   │   └── …
│   ├── cases/                  # JSON fixture regression suite
│   │   ├── graph_factory.py    # Builds OntologyGraph from input.json
│   │   └── test_case_bank.py
│   ├── helm_cases/             # Helm native case regression suite
│   │   ├── helm_case_factory.py
│   │   └── test_helm_case_bank.py
│   └── integration/
│       ├── cases/              # ← Native K8s integration test cases
│       │   ├── case_loader.py  #   kube/*.yaml + helm/ + policy/ → OntologyGraph
│       │   ├── h001_crashloopbackoff/   # CrashLoopBackOff — missing secret
│       │   ├── h002_imagepullbackoff/   # image tag drift + 401 Unauthorized
│       │   ├── h003_oomkilled/          # memory limit drift 512Mi → 128Mi
│       │   ├── h004_missing_configmap/  # CreateContainerConfigError — 3 missing resources
│       │   ├── h005_rbac_forbidden/     # SA exists, no ClusterRoleBinding → 403 Forbidden
│       │   └── h006_networkpolicy_blocked/ # egress: [] → DNS + DB + Redis blocked
│       └── use_cases/          # Dialogue simulator + proposal engine
│
├── tools/                      # Dev utilities (case contract, recalibration)
├── demo/                       # Local demo on k3d
├── k8s/                        # Production K8s manifests
├── Dockerfile
├── .env.example
└── requirements.txt
```

---

## Adding a new integration test case

1. Create `tests/integration/cases/hNNN_name/` with:
   ```
   kube/pod.yaml          # kubectl get pod -o yaml
   kube/events.yaml       # kubectl get events --field-selector involvedObject.name=… -o yaml
   helm/values.yaml       # declared chart values
   helm/release.json      # helm get values RELEASE -n NS -o json
   policy/                # optional: kubectl get policyreport -o yaml
   expect.json            # test expectations
   ```
2. Create `tests/unit/test_hybrid_pipeline_NNN.py` to register the case in the UI dropdown and add pipeline assertions.
3. The case appears automatically in **🧪 Integration Tests** → pipeline trace.

---

## RBAC

KubeWhisperer needs **read-only** cluster access. The `ClusterRole` in `k8s/rbac.yaml` grants
`get`, `list`, `watch` on all core resource types, `apps`, `batch`, `networking.k8s.io`,
`autoscaling`, and non-resource URLs for API discovery.
No `create`, `update`, `patch`, or `delete` permissions are granted.

---

## Roadmap

### Done

- [x] **LangGraph multi-path workflow** — hypothesize → analyze → retry / archive_path → select_best → dry_run → human_review
- [x] **AnchorEngine** — manifest + schema anchors; `anchor_fix_hints()` generates `helm upgrade --set` commands; **anchor pivot table** in UI (declared → observed → status → fix)
- [x] **BM25 + FAISS hybrid retrieval** — K8s-aware BM25 tokeniser + FAISS dense cosine + Reciprocal Rank Fusion; `retrieval_stats` (dense/sparse/fused/top_rrf_score) in UI
- [x] **Integration test cases — native format** — `tests/integration/cases/` with real K8s YAML (pod, events, values.yaml, helmfile, PolicyReport); unified `case_loader.py`
- [x] **Deployment readiness detection** — `_detect_missing_deps()` scans pod specs for all resource references (secrets, configmaps, PVCs, imagePullSecrets, serviceaccounts, RBAC, NetworkPolicy egress); generates `missing.*` / `netpol.*` annotations; `anchor_fix_hints()` produces concrete `kubectl create/edit` commands; h004/h005/h006 cases cover the full range
- [x] **Pipeline trace UI** — 10-step pre-LLM pipeline visualization (auto-runs on case select, no Ollama needed); Step 10 proposes values.yaml diffs + helm commands + OPA/Kyverno fixes
- [x] **RemediationEngine** — rule-based weighted hypotheses for LOW-confidence fallback; integrated in pipeline trace Bonus step
- [x] **OPA / Kyverno policy integration** — `PolicyCollector` ingests `PolicyReport` / `ClusterPolicyReport`; violations as `HAS_POLICY_VIOLATION` edges; confidence boost; fix hints
- [x] **Helm / Helmfile KB tab** — upload/paste `values.yaml`, `helmfile.yaml`, `.tgz` archives; indexed as `source=helm` documents in FAISS
- [x] **GitOps diff** — `helm template` rendered manifests vs live cluster; `LocalGitProvider` + `GithubProvider`
- [x] **Enterprise Knowledge Base** — DocStore + DocIndexer; runbooks, SOPs, Confluence, Helm charts indexed into FAISS
- [x] **Versioned K8s docs** — fetch & index official K8s docs at detected cluster version
- [x] **PatchTST signals** — multi-horizon anomaly detection on real Prometheus time series (1h/24h/7d)
- [x] **Prometheus alert correlation** — firing alerts ingested; label-matched to K8s entities; `[CRITICAL]` context section
- [x] **OTel traces** — error spans from Tempo/Jaeger; `HAS_TRACE` edges; `[TRACES]` context section
- [x] **Loki logs** — pod logs via LogQL; log level + trace IDs; `[LOGS]` context section
- [x] **Metrics server** — live CPU/memory from `metrics.k8s.io/v1beta1`; seeds PatchTST
- [x] **Pre-LLM confidence scoring** — `compute_confidence()` weights BFS, Jaccard, TF-IDF, anchors, signals, policy violations into 0–1 score
- [x] **Source weights** — per-source score multipliers; configurable via `SOURCE_WEIGHT_*` env vars

### Next

- [ ] **More h-series cases** — h007 PVC not bound, h008 Kyverno violation at admission, h009 resource quota exceeded, …
- [ ] **Helmfile multi-release** — h00N case with `helmfile.yaml` covering multiple interdependent releases
- [ ] **Multi-cluster support** — analyse multiple contexts in one session
- [ ] **Slack / PagerDuty enrichment** — push RCA summary via webhook
- [ ] **RBAC-aware scoping** — per-namespace analysis with service-account impersonation

---

## License

[Apache 2.0](LICENSE)
