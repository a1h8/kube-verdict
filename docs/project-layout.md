# Project layout

```
kubeverdict/
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

## RBAC

KubeVerdict needs **read-only** cluster access. The `ClusterRole` in `k8s/rbac.yaml` grants
`get`, `list`, `watch` on all core resource types, `apps`, `batch`, `networking.k8s.io`,
`autoscaling`, and non-resource URLs for API discovery.
No `create`, `update`, `patch`, or `delete` permissions are granted.
