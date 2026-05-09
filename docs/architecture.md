# Architecture

KubeWhisperer is a local, air-gapped Kubernetes RCA tool. No cluster data ever leaves the node.

## Pipeline overview

```
┌───────────────────────────────────────────────────────────────────────┐
│  Ingestion layer                                                      │
│                                                                       │
│  K8sCollector          HelmCollector        HelmfileCollector         │
│  (API server)          (helm CLI)           (YAML parsing)            │
│       │                     │                     │                   │
│       └─────────────────────┴─────────────────────┘                   │
│                             │                                         │
│                       OntologyGraph                                   │
│                  (typed entities + edges)                             │
│                             │                                         │
│                       HelmDriftDetector                               │
│              (declared vs observed comparison)                        │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│  Vector store                                                         │
│                                                                       │
│  Embedder (all-MiniLM-L6-v2)  →  FAISSStore (IndexFlatIP, L2 norm)    │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│  Deduplication pipeline                                               │
│                                                                       │
│  1. BFS traversal  — seeds: failed pods, degraded deploys, events     │
│  2. FAISS search   — top-k semantic neighbours                        │
│  3. Jaccard dedup  — discard chunks with token overlap > threshold    │
│  4. TF-IDF ranking — (1,3) trigrams, K8s token pattern                │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│  Context window (ContextWindow dataclass)                             │
│                                                                       │
│  [CRITICAL] seeds   — failed/degraded entities (always included)      │
│  [CRITICAL] drift   — Helm declared ≠ K8s observed (always included)  │
│  [WARNING]  events  — sorted by count desc, cap 15                    │
│  [Helm]     releases + charts                                         │
│  [Related]  BFS + FAISS + dedup neighbours                            │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│  LLM inference                                                        │
│                                                                       │
│  OllamaClient  →  Mistral (local, temperature=0.1)                    │
│  /api/generate or /api/chat (streaming)                               │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│  RCAReport                                                            │
│                                                                       │
│  summary · affected · root_cause · causal_chain · remediation         │
│  confidence · context_stats · raw_analysis                            │
└───────────────────────────────────────────────────────────────────────┘
```

## LangGraph workflow

KubeWhisperer wraps the entire pipeline in a stateful LangGraph graph with a human-in-the-loop interrupt.

```
START
  │
ingest          K8s API + Helm + Helmfile → OntologyGraph
  │
index           embed entities → FAISSStore
  │
signal_analysis PatchTST anomaly detection → signal.* annotations
  │
analyze   ◄─────────────────────────────────────────┐
  │                                                  │ retry
confidence_router                                    │ (LOW confidence,
  ├─ "retry" ──► increment_retry ────────────────────┘  < 2 attempts)
  │
  └─ "review"
       │
human_review    [INTERRUPT — waits for operator decision]
  │
human_router
  ├─ "approve" ──► remediation ──► END
  └─ "reject"  ──────────────────► END
```

Heavy objects (`OntologyGraph`, `FAISSStore`) are **never stored in LangGraph state** — they are injected via `config["configurable"]` to avoid msgpack serialisation failures with `MemorySaver`. Only JSON-serialisable primitives live in `RCAState`.

### State (`workflow/state.py`)

| Field | Type | Description |
|---|---|---|
| `query` | str | Operator's diagnostic question |
| `raw_analysis` | str | Full LLM response text |
| `confidence` | str | `HIGH` / `MEDIUM` / `LOW` |
| `report_dict` | dict | Parsed report fields |
| `retry_count` | int | Incremented each retry loop |
| `human_decision` | str | `approve` or `reject` |
| `kube_version` | str | Detected server version |
| `error` | str | Node error (if any) |

## Signal analysis (`signals/`)

| File | Purpose |
|---|---|
| `patchtst_detector.py` | PatchTST forecasting-based anomaly detector + z-score fallback |
| `analyzer.py` | `SignalAnalyzer` — derives signals from entity attributes, annotates graph |

### PatchTST anomaly detection strategy

Each entity produces one or more metric signals derived from point-in-time K8s attributes:

| Signal | Source | Synthetic history pattern |
|---|---|---|
| `restart_count` | Pod | Stable at 0, ramps to `restart_count` in last 30% |
| `ready_ratio` | Deployment / StatefulSet | Stable at 1.0, degrades in last 20% |
| `event_count` | Warning events | Near-zero baseline with spike at last 15% |

**Detection flow:**
1. **Z-score fallback** — if signal is shorter than `context_length + prediction_length`, use max z-score over the last 25% of values.
2. **PatchTST path** — normalise signal → train on first 80% with sliding windows → evaluate on last 20% → `score = eval_RMSE / baseline_RMSE`.
3. **Severity thresholds** — `score < warning_threshold` → `normal`; `< critical_threshold` → `warning`; else → `critical`.

Results are written as `signal.<metric>` annotations on entities. The `SIGNAL=[...]` block appears in `entity.to_text()`, making anomalies visible in FAISS searches and in the LLM context window.

## Key components

### Ontology (`ontology/`)

| File | Purpose |
|---|---|
| `entities.py` | 15 typed dataclasses (Pod, Deployment, …) + HelmChart, DriftItem |
| `graph.py` | `OntologyGraph` — nodes, directed edges, BFS, server version |
| `relationships.py` | 14 edge types: `RUNS_ON`, `OWNED_BY`, `DRIFTS_FROM`, … |
| `version.py` | `KubeVersion` — feature flags for API deprecations (Ingress v1/v1beta1, CronJob, HPA) |
| `discovery.py` | `APIServerDiscovery` — dynamic enumeration of all resource kinds via `/apis` |
| `dynamic_entity.py` | `GenericEntity` — CRDs and unknown resource types |

### Ingestion (`ingestion/`)

| File | Purpose |
|---|---|
| `k8s_collector.py` | Collects all K8s resources; version-aware API choices |
| `helm_collector.py` | `helm get values --all` + chart parsing per release |
| `helm_drift.py` | Compares Helm-declared state vs live K8s state, writes `drift.*` annotations |
| `helmfile_collector.py` | Pure YAML parsing (no Go template engine needed), env value merge |
| `chart_parser.py` | Parses Chart.yaml, umbrella deps, value hierarchy, tgz support |

### Deduplication (`dedup/`)

| File | Purpose |
|---|---|
| `bfs.py` | Graph traversal seeded from unhealthy entities |
| `jaccard.py` | Greedy token-level dedup, O(n²) on kept items |
| `tfidf.py` | `TfidfVectorizer(ngram_range=(1,3))` with K8s-aware token pattern |

### Vector store (`vectorstore/`)

| File | Purpose |
|---|---|
| `embedder.py` | `sentence-transformers/all-MiniLM-L6-v2`, L2 normalisation |
| `store.py` | `FAISSStore` — `IndexFlatIP` (cosine via inner product on normalised vecs), save/load |

### RCA (`rca/`)

| File | Purpose |
|---|---|
| `context_builder.py` | Assembles `ContextWindow` — 5 labelled sections fed to Mistral |
| `analyzer.py` | `RCAAnalyzer.analyze()` / `stream_analyze()`, `RCAReport` with parsed structured fields |

### LLM (`llm/`)

| File | Purpose |
|---|---|
| `ollama_client.py` | `/api/generate`, `/api/chat`, streaming, health checks |

## Multi-version Kubernetes

`detect_version()` hits `/version` at startup and populates `KubeVersion`. All version-dependent
API choices are driven by feature flags:

| Flag | Threshold | Old API | New API |
|---|---|---|---|
| `ingress_api_version` | ≥ 1.19 | `networking.k8s.io/v1beta1` | `networking.k8s.io/v1` |
| `cronjob_api_version` | ≥ 1.21 | `batch/v1beta1` | `batch/v1` |
| `hpa_api_version` | ≥ 1.26 | `autoscaling/v2beta2` | `autoscaling/v2` |
| `has_pod_security_policy` | < 1.25 | PSP present | PSP removed |

K3s suffixes (`v1.28.3+k3s1`) are parsed correctly by the `_parse_int()` regex.

## Drift detection

`HelmDriftDetector` runs after ingestion and checks:
- `spec.replicas` declared in Helm values vs `.status.readyReplicas` in K8s
- PVC phase (Pending = volume not bound)
- Container restart count > 5
- `CrashLoopBackOff` / `OOMKilled` in container state reasons
- Sub-chart enabled/disabled conditions for umbrella charts

Drift items are written as `drift.*` annotations on entities so they surface in FAISS searches and are always included in the `[CRITICAL]` section of the context window.

## TF-IDF token pattern

```
[A-Za-z0-9_.=\-/+:]{2,}
```

This preserves K8s compound tokens that standard tokenizers split incorrectly:

| Token | Meaning |
|---|---|
| `phase=Failed` | key=value pair |
| `apps/v1` | API group/version |
| `v1.28.3+k3s1` | K3s server version |
| `CrashLoopBackOff` | CamelCase reason |
| `nginx:1.21` | image:tag |

Trigrams (`ngram_range=(1,3)`) boost multi-token diagnostic phrases like
`phase=Failed restarts=15 CrashLoopBackOff` or `declared=3 observed=0 severity=critical`.
