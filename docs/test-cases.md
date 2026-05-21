# Integration test cases

## Native Kubernetes format

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

## Adding a new case

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

The `test_hybrid_pipeline_NNN.py` files are also the **registration mechanism** for the UI dropdown — creating one registers the corresponding case in the Integration Tests tab.

## Running tests

```bash
pytest                               # all tests (unit + cases + integration)
pytest tests/unit/                   # unit only — no cluster, no LLM
pytest tests/cases/                  # offline JSON fixture regression (20 scenarios)
pytest tests/unit/test_hybrid_pipeline_001.py  # h001 CrashLoopBackOff pipeline
pytest tests/unit/test_hybrid_pipeline_002.py  # h002 ImagePullBackOff pipeline
pytest tests/integration/            # pipeline tests with mock LLM
pytest --cov=. --cov-report=term-missing
```

## Validated demo scope

The table below distinguishes what is **proven offline** (runs in CI, no cluster, no Ollama) from what requires a **live environment**.

| Scenario | Case | Runs in CI | What it proves |
|---|---|---|---|
| CrashLoopBackOff — missing dependency | h001 | ✅ | BFS graph traversal, BM25+FAISS retrieval, anchor detection, confidence scoring, fix proposals |
| ImagePullBackOff — registry auth / tag drift | h002 | ✅ | Helm drift detection, `drift.*` annotations, image proposal generation |
| OOMKilled — memory limit drift | h003 | ✅ | Helm declared-vs-observed diff, `anchor_fix_hints()` → `helm upgrade --set` |
| Missing ConfigMap / Secret at pod start | h004 | ✅ | `DeploymentReadinessDetector`, `missing.*` annotations, `kubectl create` hints |
| RBAC — missing ClusterRoleBinding | h005 | ✅ | SA exists but no binding detected, `kubectl create clusterrolebinding` hint |
| NetworkPolicy egress block | h006 | ✅ | `netpol.*` annotations, `kubectl edit networkpolicy` hints |

**Each CI run** (`pytest tests/unit/test_hybrid_pipeline_NNN.py`) validates the full pre-LLM pipeline — graph construction, hybrid retrieval (BM25 + FAISS + RRF), context building, anchor/drift/policy scoring, and proposal generation — against a fixed JSON fixture. No Ollama, no cluster.

Components that require a **live environment** (not in CI scope):
- Live Kubernetes API calls (`k8s_collector.py`, `metrics_server_collector.py`)
- Prometheus / Alertmanager scrape (`prometheus_collector.py`)
- OTel backends — Tempo / Jaeger (`otel_collector.py`)
- Loki log queries (`loki_source.py`)
- Ollama LLM inference (multi-path hypothesis reasoning)
- PatchTST anomaly forecasting on real time series
- GitOps diff via `helm template` + GitHub API
