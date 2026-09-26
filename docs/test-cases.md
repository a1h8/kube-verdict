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
├── h006_networkpolicy_blocked/ ← egress: [] blocks DNS + PostgreSQL + Redis; pod Running but not Ready
├── h007_hpa_no_metrics/      ← HPA stuck, metrics-server not returning pod metrics
├── h008_init_container_fail/ ← Init:0/1 — db-migrate init container exits 1
├── h009_liveness_probe_loop/ ← liveness timeoutSeconds drift (1s deployed vs 5s declared) restarts healthy pods
├── h010_resource_quota_exceeded/ ← pod Pending, namespace ResourceQuota CPU cap full
├── h011_statefulset_pvc_stuck/ ← StatefulSet rolling update stuck, PVC bound to old pod
├── h012_gitops_render_vs_live/ ← `helm template` expected state diffed vs live (render-vs-live wedge)
├── h013_slo_error_budget_burn/ ← healthy pod, p99/p95 latency-SLO breach + burn-rate alerts (Prometheus fixtures)
│   └── prometheus/           ← raw `/api/v1/alerts` fixtures (incl. a pending + an uncorrelated decoy)
├── h014_cert_expiry/         ← expired mTLS cert (cert-manager issuer failing); pods Running but not Ready, cause only in logs
│   └── loki/                 ← Loki `query_range` stream fixtures (x509: certificate has expired)
└── h015_etcd_compaction/     ← pod Ready=False on readiness timeout; cause only visible in OTel error traces
    └── otel/                 ← normalized error-trace fixtures (DeadlineExceeded on etcd Range)
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
   otel/*.json            # optional: list of normalized error traces (OtelBackend.search_error_traces shape)
   prometheus/*.json      # optional: list of raw alerts in Prometheus GET /api/v1/alerts shape
   loki/*.json            # optional: list of Loki query_range streams ({stream: {k8s_pod_name, ...}, values: [[ts_ns, line], ...]})
   expect.json            # test expectations
   ```
   `prometheus/` fixtures are fed through the **real** `PrometheusCollector.collect()` (its HTTP fetch swapped for the fixture list), so label→entity correlation, `HAS_ALERT` edges and `alert.*` annotations match a live cluster — non-`firing` alerts and alerts with no matching entity are dropped exactly as they would be live. `otel/` fixtures go through the **real** `OtelCollector.collect()` too: a fixture backend answers by `service_name` like a live one, so target selection (`Pod.needs_telemetry` — Running-but-not-ready pods included — and degraded workloads), service-label resolution, `HAS_TRACE` edges and `otel.trace.*` annotations are the live code path, and a trace for a service absent from the snapshot is never collected. `loki/` streams go through the **real** `LokiSource.collect()` as well: only its private `_query` is replaced, matching the LogQL label matchers against each stream's labels like Loki does, so pod selection (`Pod.needs_telemetry`), the LogQL, level detection, `LokiLog` nodes and `HAS_LOG` edges are the live code path (fixtures are frozen in time, so the query time window is not applied).
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
| HPA cannot scale — metrics-server unavailable | h007 | ✅ | HPA target resolution, metrics-unavailable detection, `metrics-server` fix hints |
| Init container failing — DB migration exits 1 | h008 | ✅ | `Init:0/1` detection, init-container log correlation, migration-failure root cause |
| Liveness probe too aggressive | h009 | ✅ | probe-timeout drift (`timeoutSeconds` declared vs deployed) → `helm upgrade` fix |
| ResourceQuota exceeded — pod Pending | h010 | ✅ | `ResourceQuota` entity, namespace quota correlation, pending-pod root cause |
| StatefulSet update stuck — PVC bound to old pod | h011 | ⚠️ | wired in via `test_native_helm_dialogue`, but `test_confidence_score_min` and `test_has_resolvable_path` still fail — open contribution |
| SLO error-budget burn — p99/p95 latency breach on a pod Kubernetes reports healthy | h013 | ✅ (evidence wiring) | `tests/integration/test_prometheus_fixture_h013.py`: 0 seeds / 0 drift / 0 events yet 3 firing SLO alerts reach `ContextWindow.alerts` (critical first) via the real `PrometheusCollector`; a `pending` alert and an uncorrelated alert are correctly dropped |
| Expired mTLS certificate — cert-manager issuer cannot renew, readiness fails on every replica | h014 | ✅ (evidence wiring) | `tests/integration/test_loki_fixture_h014.py`: pods Running but not Ready are selected by `Pod.needs_telemetry` and their logs reach `ContextWindow.logs` through the real `LokiSource` (error/warn only). Events show the symptom and an issuer warning but never `x509`/`expired`; only the logs do. A same-named pod in another namespace is not returned, and under the old phase-only selection no log is fetched at all |
| etcd compaction latency — readiness timeout with no cause in K8s events | h015 | ✅ (evidence wiring) | `tests/integration/test_otel_fixture_h015.py`: via the real `OtelCollector`, 4 OTel error traces reach both the Running-but-not-ready pod and the degraded Deployment (`HAS_TRACE`) → `ContextWindow.traces` → prompt; a decoy trace for another service is dropped, and a test shows the pod link disappears under the old phase-only selection |

> **Scope of the h013 / h014 / h015 checks.** They prove the *evidence path* — fixture → graph → context window — deterministically in CI. They do **not** assert the LLM's final root-cause text: that needs Ollama and runs via the generic `test_native_helm_dialogue` (which picks these cases up automatically) wherever a model is available.

Live captures (`tools/b13_capture.py`) get the same root-cause check, against
ground truth, outside CI: see [veracity-benchmark.md](veracity-benchmark.md)
— it's how the first two live h014/h015 captures were caught getting the root
cause wrong (both graded `FAIL`) instead of that only being visible by
re-reading `docs/evidence/prometheus-live.md` prose by hand.

**Each CI run** (`pytest tests/unit/test_hybrid_pipeline_NNN.py`) validates the full pre-LLM pipeline — graph construction, hybrid retrieval (BM25 + FAISS + RRF), context building, anchor/drift/policy scoring, and proposal generation — against a fixed JSON fixture. No Ollama, no cluster.

Components that require a **live environment** (not in CI scope):
- Live Kubernetes API calls (`k8s_collector.py`, `metrics_server_collector.py`)
- Prometheus / Alertmanager scrape (`prometheus_collector.py`) — the *network fetch* only; its correlation/annotation logic is exercised offline by h013
- OTel backends — Tempo / Jaeger (`otel_collector.py`) — the *backend query* only; the collector's target selection, service resolution and graph wiring are exercised offline by h015 through the real `OtelCollector`
- Loki log queries (`loki_source.py`) — the *HTTP query* only; pod selection, LogQL, level detection and graph wiring are exercised offline by h014 through the real `LokiSource`
- Ollama LLM inference (multi-path hypothesis reasoning)
- PatchTST anomaly forecasting on real time series
- GitOps diff via `helm template` + GitHub API
