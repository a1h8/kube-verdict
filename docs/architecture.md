# Architecture

KubeVerdict is a local, air-gapped Kubernetes RCA tool. No cluster data ever leaves the node.

## Anchor-by-render

The organising idea: KubeVerdict does not diagnose from the live cluster alone. It first
reconstructs the **expected** state by rendering Helm/GitOps manifests (`helm template` with
the full Helmfile value hierarchy), then compares that rendered intent against the observed
cluster. Drift between the two becomes first-class RCA evidence — not a sync trigger.

This is implemented by two cooperating layers, both detailed below:

- **GitOps diff layer** — `GitopsCollector` → `ManifestRenderer` (`helm template`) →
  `ManifestDiffer` (rendered vs observed → `gitops.*` annotations). See *GitOps diff layer*.
- **Anchor layer, Source 2** — `AnchorEngine` reuses the same rendered output to extract the
  *exact declared field values* (`spec.replicas`, image tags, resources…) as drift anchors,
  with no heuristic Helm-value → K8s-field mapping. See *Anchor system design → Source 2*.

The rendered path is **opt-in**: the `gitops` node is skipped when `GITOPS_ENABLED=false` or no
`GITOPS_REPO_URL` is set, and the `AnchorEngine` then relies on Source 1 (K8s schema) plus the
ingestion-time `HelmDriftDetector` (Helm-declared values vs live). The currently validated
scenario set (h001–h010) exercises that Helm-values-drift path; a dedicated render-vs-live
scenario is the next step to back the rendered path with a validated case. The narrative version
of this concept lives in [anchor-by-render.md](anchor-by-render.md).

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
│     ┌───────────────────────┼───────────────────────┐                │
│     │                       │                       │                │
│  HelmDriftDetector  MetricsServerCollector  PrometheusCollector       │
│ (declared vs         (metrics.k8s.io        (firing alerts →          │
│  observed)           → cpu_m / memory_mi)   alert.* annotations)     │
│                                       │                              │
│                              OtelCollector + LokiSource               │
│                         (error traces + pod logs →                    │
│                          HAS_TRACE / HAS_LOG edges)                   │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│  GitOps diff layer                                                    │
│                                                                       │
│  GitopsCollector                                                      │
│  ├── GitProvider: LocalGitProvider (clone) or GithubProvider (API)    │
│  ├── ManifestRenderer: helm template → []dict                         │
│  └── ManifestDiffer: rendered vs observed → DriftItem list            │
│      ├── MISSING   rendered resource absent from cluster  (critical)  │
│      ├── ORPHANED  cluster resource not in rendered       (warning)   │
│      ├── REPLICAS  spec.replicas mismatch                 (warning)   │
│      ├── IMAGE     container image tag mismatch           (warning)   │
│      └── ENV       sensitive env var mismatch             (info)      │
│                                                                       │
│  Results written as gitops.* annotations on entities                  │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│  Anchor layer                                                         │
│                                                                       │
│  AnchorEngine.collect(graph, provider)                                │
│                                                                       │
│  Source 1 — K8s schema (always)                                       │
│    K8sApiSchema: valid values, defaults for Pod/Deployment/…          │
│    Enriched from live /openapi/v2 if API server is reachable          │
│                                                                       │
│  Source 2 — Rendered manifests (when gitops provider available)       │
│    Same helm template output as GitOps diff — no second render        │
│    _extract_manifest_fields → exact declared K8s field values         │
│    Helmfile environment value_files prepended (-f env.yaml first)     │
│    Full hierarchy: chart < env value_files < release value_files      │
│                    < inline values                                    │
│                                                                       │
│  Why not heuristic Helm-value → K8s field mapping?                    │
│    Heuristics (replicaCount → spec.replicas) only work for charts     │
│    following community naming conventions.  helm template is the      │
│    generic ground truth — it handles all GoTemplate conditionals,     │
│    feature gates (enabled: false → block absent), loops, and custom   │
│    naming without any additional mapping logic.                       │
│                                                                       │
│  Dedup: manifest (priority 2) > k8s_defaults (priority 1)            │
│  Results written as anchor.* annotations on entities                  │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│  Signal analysis                                                      │
│                                                                       │
│  SignalAnalyzer (PatchTST + z-score fallback)                         │
│  Results written as signal.* annotations on entities                  │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│  Vector store                                                         │
│                                                                       │
│  Embedder (all-MiniLM-L6-v2)  →  FAISSStore (IndexFlatIP, L2 norm)    │
│  index_graph()          — all entities (anchor.*, gitops.*, signal.*) │
│  index_anchor_violations() — each anchor drift field as a separate    │
│                              doc_source="anchor" entry (×1.6 weight)  │
│                                                                       │
│  Hybrid retrieval:                                                    │
│    hybrid_search(query) = FAISS dense + BM25Okapi sparse              │
│                           → rrf_fuse() → SOURCE_WEIGHTS boost         │
│    SOURCE_WEIGHTS: cluster=1.0  official=1.0  example=1.2             │
│                    anchor=1.6   enterprise=1.5  runbook=1.8           │
│                                                                       │
│  BM25Retriever — Okapi BM25 over entity texts (k1=1.5, b=0.75)        │
│  Anchor hits naturally surface above plain cluster entities           │
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
│  Context window (ContextWindow dataclass — 9 sections)                │
│                                                                       │
│  [CRITICAL] seeds    — failed/degraded entities (always included)     │
│  [CRITICAL] drift    — Helm declared ≠ K8s observed (always included) │
│  [CRITICAL] alerts   — firing Prometheus alerts (critical first)      │
│  [TRACES]   traces   — OTel error spans (cap 20)                      │
│  [LOGS]     logs     — Loki error/warn lines (cap 20)                 │
│  [WARNING]  events   — sorted by count desc (cap 15)                  │
│  [ANCHORS]  anchors  — declared values & K8s schema (cap 30)          │
│             Priority: unhealthy/drifted entities first                │
│             Format: "Deployment/prod/api: spec.replicas:              │
│                      declared='5' [manifest] | k8s_default='1'"       │
│  [Helm]     releases + charts                                         │
│  [Related]  BFS + FAISS + dedup neighbours (cap TFIDF_TOP_K)          │
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
│                                                                       │
│  Rule-based fallback (RemediationEngine)                              │
│    When LLM returns LOW confidence or empty structured fields,         │
│    7 weighted rules (OOMKill, CrashLoop, ImagePull, MissingConfig,    │
│    PendingScheduling, HelmDrift, DegradedDeployment) populate         │
│    summary/root_cause/causal_chain/affected from hypothesis scores.   │
│    Confidence label: "LOW (rule-assisted — top: {rule} w=0.85)"       │
└───────────────────────────────────────────────────────────────────────┘
```

## LangGraph workflow

```
START
  │
ingest          K8s API + Helm + Helmfile → OntologyGraph
  │
metrics         MetricsServerCollector → metrics.cpu_m / metrics.memory_mi on pods
  │             (opt-in: METRICS_SERVER_ENABLED=true)
prometheus      PrometheusCollector → firing alerts → alert.* annotations
  │             (opt-in: PROMETHEUS_ENABLED=true)
otel            OtelCollector (Tempo/Jaeger) + LokiSource → HAS_TRACE / HAS_LOG edges
  │             (opt-in: OTEL_ENABLED=true, LOKI_ENABLED=true)
gitops          GitopsCollector → ManifestRenderer → ManifestDiffer
  │             (skipped when GITOPS_ENABLED=false or no GITOPS_REPO_URL)
anchor          AnchorEngine → K8s schema + rendered manifests → anchor.*
  │
index           embed entities → FAISSStore
  │             + index_anchor_violations() → doc_source="anchor" entries (×1.6 weight)
signal_analysis PatchTST anomaly detection → signal.* annotations
  │
hypothesize     Evidence-first — LLM only fills remaining slots (see below)
  │
example_lookup  hybrid_search → score ≥ 0.65 → skip analyze loop (known fix)
  ├─ "skip"  ──────────────────────────────────────────────────► select_best
  └─ "analyze"
       │
analyze   ◄─────────────────────────────────────────┐
  │                                                  │ retry
log_confidence_decision ──► confidence_router        │ (BFS depth widens by 1)
  ├─ "retry"     LOW × 1, not yet declining ─────────┘
  ├─ "next_path" LOW × 2 (declining) or retries exhausted + candidates remain
  │                   ──► archive_path (re-rank candidates) ──► analyze (new H)
  └─ "review"    HIGH/MEDIUM  or  LOW + no candidates left
       │
dry_run         Each remediation command executed in dry-run mode
  │
select_best     Restore highest-confidence path from reasoning_history
  │
human_review    [INTERRUPT — operator sees: report + dry-run + edge_log + sources]
  │
log_human_decision ──► human_router
  ├─ "approve" ──► remediation ──► save_example ──► END
  └─ "reject"  ──────────────────────────────────► END
```

### Evidence-first hypothesis generation (`hypothesize_node`)

The LLM is a **next-token predictor over the top-k context window** — it does not reason from scratch. Hypotheses are built from structured evidence in probability order:

```
Phase 1+2b  hybrid_search(query, top_k=10)          ← single FAISS+BM25+RRF call
            ├─ uid starts with "example:"  → extract Hypothesis: field
            │   weight = cosine_score + 1.0  (proven resolutions win)
            └─ uid starts with "anchor:"   → _anchor_hit_to_hypothesis(text)
                weight = 0.88             (manifest drift evidence)

Phase 2     RemediationEngine.score(graph)[:5]
            weight = rule.weight (0.60–0.95, deterministic)

Phase 3     OntologyGraph causal chains
            Pod → USES_PVC    → PVC Pending/Failed    weight=0.85+0.10
            Pod → MOUNTS_SECRET → Secret missing      weight=0.82+0.10
            Pod → MOUNTS_CONFIGMAP → CM missing       weight=0.80+0.10
            Pod → DRIFTS_FROM → HelmRelease drift     weight=0.78
            …

Phase 4     LLM fill-in  ← only if pool < MAX_PATHS (3)
```

H1 = highest-probability path, H2/H3 = fallbacks explored if confidence declines.

### Beam search confidence routing

```
path_confidence_history = []   ← reset at each path switch

analyze → conf=LOW  → history=["LOW"]       → retry (BFS+1, context widens)
analyze → conf=LOW  → history=["LOW","LOW"] → declining=True
                                            → next_path IMMEDIATELY
                                            (don't wait for max_retries)

archive_path:
  1. Archive failed hypothesis + analysis into reasoning_history
  2. _rerank_candidates(): hybrid_search on failed raw_analysis
     → signal tokens from top-k → re-score remaining candidates
     → highest-overlap candidate becomes new H1
  3. Reset path_confidence_history = []
```

The global probability is maximised by abandoning stagnant paths early and re-ranking remaining candidates with the evidence accumulated so far.

### FastAPI REST API (`api/`)

```
POST   /api/v1/sessions              Create session
POST   /api/v1/sessions/{id}/run     Start analysis (async, background task)
GET    /api/v1/sessions/{id}/state   Poll current state
GET    /api/v1/sessions/{id}/stream  SSE stream (one JSON event per state update)
POST   /api/v1/sessions/{id}/feedback  approve | reject | extra_context re-run
DELETE /api/v1/sessions/{id}         Clean up
GET    /api/v1/health
```

`SessionState` response includes: `reasoning_history`, `hypothesis_sources`, `path_confidence_history`, `edge_log` (router decisions with `declining` flag), `dry_run_results`, `review_payload`.

The `gitops` node auto-detects provider from `GITOPS_REPO_URL`:
- `https://github.com/…` → `GithubProvider` (REST API, no local clone, needs `GITHUB_TOKEN`)
- any other URL or local path → `LocalGitProvider` (shallow clone, fast-forward pull)

Heavy objects (`OntologyGraph`, `FAISSStore`, `GitProvider`) are **never stored in LangGraph state** — they are injected via `config["configurable"]` to avoid msgpack serialisation failures with `MemorySaver`. Only JSON-serialisable primitives live in `RCAState`.

### State (`workflow/state.py`)

| Field | Type | Description |
|---|---|---|
| `query` | str | Operator's diagnostic question |
| `raw_analysis` | str | Full LLM response text |
| `confidence` | str | `HIGH` / `MEDIUM` / `LOW` |
| `report_dict` | dict | Parsed report fields |
| `retry_count` | int | Incremented each retry on current path |
| `human_decision` | str | `approve` or `reject` |
| `kube_version` | str | Detected server version |
| `error` | str | Node error (if any) |
| `candidate_paths` | list[str] | Remaining hypotheses to explore (FIFO) |
| `current_hypothesis` | str | Hypothesis under analysis |
| `reasoning_history` | list[dict] | Archived paths: step, hypothesis, confidence, summary |
| `hypothesis_sources` | list[dict] | Rule/anchor/ontology evidence behind each hypothesis |
| `path_confidence_history` | list[str] | Confidence sequence for current path (beam search) |
| `edge_log` | list[dict] | Router decisions: router, edge_taken, reason, snapshot, ts |
| `dry_run_results` | list[dict] | Dry-run output per remediation command |
| `example_match` | bool | True when example_lookup short-circuited the analyze loop |

## Key components

### Ontology (`ontology/`)

| File | Purpose |
|---|---|
| `entities.py` | 18 typed dataclasses: Pod, Deployment, StatefulSet, DaemonSet, Service, PVC, HPA, HelmRelease, HelmChart, HelmfileEnvironment, K8sEvent, PrometheusAlert, OtelTrace, LokiLog, ConfigMap, Secret, + DriftItem/ChartDependency |
| `graph.py` | `OntologyGraph` — nodes, directed edges, BFS, server version |
| `relationships.py` | 16 edge types: `RUNS_ON`, `OWNED_BY`, `DRIFTS_FROM`, `HAS_ALERT`, `HAS_LOG`, `HAS_TRACE`, … |
| `version.py` | `KubeVersion` — feature flags for API deprecations |
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
| `git_provider.py` | `LocalGitProvider` (shallow clone) + `GithubProvider` (REST API, base64) |
| `manifest_renderer.py` | `ManifestRenderer` — wraps `helm template --include-crds`, multi-doc YAML |
| `manifest_differ.py` | `ManifestDiffer` — rendered vs observed drift (MISSING/ORPHAN/IMAGE/REPLICAS/ENV) |
| `gitops_collector.py` | `GitopsCollector` — orchestrates provider → render → diff → annotate |
| `k8s_schema.py` | `FieldMeta` + `_SCHEMA` — embedded K8s field defaults and valid values for 7 resource kinds; `K8sApiSchema` optionally enriches from live `/openapi/v2` |
| `anchor_engine.py` | `AnchorEngine` — collects declared-value anchors from K8s schema and rendered manifests, writes `anchor.*` annotations; `AnchorRecord` data model |
| `metrics_server_collector.py` | `MetricsServerCollector` — queries `metrics.k8s.io/v1beta1` per namespace; writes `metrics.cpu_m` and `metrics.memory_mi` on `Pod` entities |
| `prometheus_collector.py` | `PrometheusCollector` — fetches firing alerts from `/api/v1/alerts`; label-matches to K8s entities (pod › deployment › statefulset › daemonset › service › node); writes `alert.*` annotations; creates `PrometheusAlert` nodes with `HAS_ALERT` edges |
| `otel_backend.py` | `OtelBackend` ABC + `TempoBackend` (Grafana Tempo `/api/search`) + `JaegerBackend` (`/api/services` + `/api/traces`); normalises traces to a common dict schema |
| `otel_collector.py` | `OtelCollector` — resolves unhealthy entities to service names; fetches error traces via `OtelBackend`; creates `OtelTrace` nodes with `HAS_TRACE` edges; writes `otel.trace.*` annotations |
| `loki_source.py` | `LokiSource` — queries `/loki/api/v1/query_range` with pod-scoped LogQL; infers log level; extracts OTel trace IDs; creates `LokiLog` nodes with `HAS_LOG` edges |

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
| `store.py` | `FAISSStore` — `IndexFlatIP`, `index_graph()`, `index_anchor_violations()`, `hybrid_search()`, `search()`, save/load |
| `bm25_retriever.py` | `BM25Retriever` — Okapi BM25 (rank_bm25) over entity texts |
| `rrf.py` | `rrf_fuse()` — Reciprocal Rank Fusion (Cormack et al. SIGIR 2009) |

#### FAISS vs Weaviate — design decision

The current default is **FAISS** (in-process, zero-dependency, runs air-gapped):

| | FAISS (default) | Weaviate (`feat/weaviate-store`) |
|---|---|---|
| **Deployment** | in-process, no service | separate container / Helm chart |
| **Persistence** | manual `save/load` to `.faiss` files | built-in, survives restarts |
| **Search** | pure vector (cosine) | hybrid: BM25 + vector (alpha-weighted) |
| **BM25 benefit** | — | exact token matches win over semantic noise |
| **Scale** | single-node, ~1M vecs fine | distributed, multi-tenant |
| **Air-gap** | yes — index rebuilt each run | needs Weaviate container (can be local) |
| **When to switch** | — | large clusters (>5k entities), persistent index, or BM25 recall matters |

### RCA (`rca/`)

| File | Purpose |
|---|---|
| `context_builder.py` | Assembles `ContextWindow` (11 sections) fed to Mistral; includes `[TRACES]` and `[LOGS]` sections from OTel/Loki; anchors section prioritises unhealthy/drifted entities |
| `analyzer.py` | `RCAAnalyzer.analyze()` / `stream_analyze()`, `RCAReport` with structured fields; rule-based fallback populates empty fields when LLM confidence is LOW |

### Remediation (`rca/remediation_engine.py`)

7 weighted rules fire against the OntologyGraph. Each rule produces a `Hypothesis` with:

| Rule | Base weight | Key boost conditions |
|---|---|---|
| `OOMKill` | 0.80 | `lastState.OOMKilled`, `requests.memory` missing (+0.10 each) |
| `CrashLoopDB` | 0.65 | restarts ≥ 5, connection-refused messages |
| `ImagePull` | 0.75 | `ErrImagePull`, `ImagePullBackOff` in state reason |
| `MissingConfig` | 0.70 | `CreateContainerConfigError`, secret/configmap not found |
| `PendingScheduling` | 0.60 | `Insufficient`, taint/toleration keywords in events |
| `HelmDrift` | 0.75 | `drift.*` annotations, unhealthy + drift compound boost |
| `DegradedDeployment` | 0.65 | readyReplicas / replicas < 0.5 |

`weight = min(1.0, base_weight + Σ boosts)`. Top hypothesis fills `summary`, `root_cause`, `causal_chain`, `affected` when LLM returns empty fields.

### LLM (`llm/`)

| File | Purpose |
|---|---|
| `ollama_client.py` | `/api/generate`, `/api/chat`, streaming, health checks |

### Signal analysis (`signals/`)

| File | Purpose |
|---|---|
| `patchtst_detector.py` | PatchTST forecasting-based anomaly detector + z-score fallback |
| `prometheus_source.py` | `PrometheusMetricSource` — fetches real time series at 3 horizons; graceful fallback to synthetic if Prometheus unavailable |
| `analyzer.py` | `SignalAnalyzer` — derives signals from real Prometheus data or entity attributes; annotates graph |

#### PatchTST anomaly detection strategy

When `PROMETHEUS_ENABLED=true`, `SignalAnalyzer` fetches real time series from `PrometheusMetricSource` at three horizons before falling back to synthetic history:

| Horizon | Lookback | Step | Purpose |
|---|---|---|---|
| `short` | 1 h | 1 m (~60 pts) | Is it getting worse right now? |
| `medium` | 24 h | 15 m (~96 pts) | When did it start / what trend? |
| `long` | 7 d | 1 h (~168 pts) | Anomaly vs normal weekly pattern? |

Metrics fetched per entity type:
- **Pod** — `restart_count` (rate), `cpu_usage` (container CPU seconds), `memory_bytes` (working set)
- **Deployment / StatefulSet** — `ready_ratio` (available / desired)

When Prometheus is unavailable or returns no data, `SignalAnalyzer` falls back to point-in-time K8s attributes anchored to real `metrics.cpu_m`/`metrics.memory_mi` from `MetricsServerCollector`:

| Signal | Source | Synthetic history pattern |
|---|---|---|
| `restart_count` | Pod | Stable at 0, ramps to `restart_count` in last 30% |
| `ready_ratio` | Deployment / StatefulSet | Stable at 1.0, degrades in last 20% |
| `event_count` | Warning events | Near-zero baseline with spike at last 15% |

**Detection flow:**
1. **Z-score fallback** — if signal is shorter than `context_length + prediction_length`, use max z-score over the last 25% of values.
2. **PatchTST path** — normalise → train on first 80% with sliding windows → evaluate on last 20% → `score = eval_RMSE / baseline_RMSE`.
3. **Severity thresholds** — `score < warning_threshold` → `normal`; `< critical_threshold` → `warning`; else → `critical`.

Results written as `signal.<metric>` annotations on entities.

## Anchor system design

The anchor system answers "what was this field supposed to be?" for every K8s entity, enabling the LLM to reason about drift without needing to understand Helm templates.

### Two generic sources

**Source 1 — K8s schema** (always available, no network required)

`K8sApiSchema` embeds field metadata for 7 resource kinds and optionally enriches from the live API server's `/openapi/v2`. Anchors include:
- `k8s_default` — the platform default when the field is omitted
- `valid_values` — the enum of legal values
- `severity_on_drift` — how serious a deviation is (critical / warning / info)

**Source 2 — Rendered manifests** (requires GitProvider)

`helm template` with the full value hierarchy produces the exact YAML that would be deployed. `_extract_manifest_fields` reads `spec.replicas`, container resources, image tags, service type, PVC modes, etc. directly from the output — no heuristic mapping needed.

The Helmfile value hierarchy passed to `helm template`:
```
-f env_value_file_1.yaml   ← HelmfileEnvironment.value_files (lowest priority)
-f release_value_file.yaml ← HelmRelease.value_files
--set key=value            ← HelmRelease.values (inline, highest priority)
```

### Why not heuristic mapping?

Heuristic approaches (mapping `replicaCount` → `spec.replicas`) only work for charts following bitnami-style naming conventions. A custom chart using `worker.replicas` or `app.instances` breaks silently. The rendered manifest is the only truly generic approach — Helm itself resolves all GoTemplate conditionals, loops, and value transformations.

### Feature gates and disabled blocks

When a value gate is false (`autoscaling.enabled: false`), the entire HPA block is absent from the rendered manifest — no false anchors are created for disabled resources. The LLM sees two complementary signals:
- **ManifestDiffer** — flags `MISSING` if a resource should exist but doesn't
- **HelmRelease context** — surfaces `autoscaling.enabled=false` in the `### Helm / Helmfile releases` section

This split is intentional: field-level anchors come from the rendered output (generic, exact); feature-gate context comes from the raw Helm values already in the context window.

## Multi-version Kubernetes

`detect_version()` hits `/version` at startup and populates `KubeVersion`. All version-dependent API choices are driven by feature flags:

| Flag | Threshold | Old API | New API |
|---|---|---|---|
| `ingress_api_version` | ≥ 1.19 | `networking.k8s.io/v1beta1` | `networking.k8s.io/v1` |
| `cronjob_api_version` | ≥ 1.21 | `batch/v1beta1` | `batch/v1` |
| `hpa_api_version` | ≥ 1.26 | `autoscaling/v2beta2` | `autoscaling/v2` |
| `has_pod_security_policy` | < 1.25 | PSP present | PSP removed |

K3s suffixes (`v1.28.3+k3s1`) are parsed correctly by the `_parse_int()` regex.

## Drift detection

### HelmDriftDetector (ingestion-time)

Runs after ingestion and checks:
- `spec.replicas` declared in Helm values vs `.status.readyReplicas` in K8s
- PVC phase (Pending = volume not bound)
- Container restart count > 5
- `CrashLoopBackOff` / `OOMKilled` in container state reasons
- Sub-chart enabled/disabled conditions for umbrella charts

Writes `drift.*` annotations → always in `[CRITICAL]` section of context window.

### ManifestDiffer (GitOps-time)

Runs inside `gitops_node` against the rendered manifest:
- `MISSING` — resource in manifest but not in cluster (critical)
- `ORPHANED` — resource in cluster but not in manifest (warning)
- `REPLICAS` — `spec.replicas` mismatch (warning)
- `IMAGE` — container image tag differs (warning)
- `ENV` — sensitive env var differs (info)

Writes `gitops.*` annotations.

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
| `declared='5'` | anchor format |
| `k8s_default='1'` | schema default format |

Trigrams (`ngram_range=(1,3)`) boost multi-token diagnostic phrases like
`phase=Failed restarts=15 CrashLoopBackOff` or `declared=5 observed=2 severity=critical`.
