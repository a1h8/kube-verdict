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

## Broadened offline coverage — instead of one live session per case

> Decision (2026-10-04): no piecemeal live sessions (e.g. a live h013 capture just to
> unblock the vitrine). Everything is proven offline first; live runs happen **once**, at
> the end, for all cases together — the `docs/cloud-prerequisites.md` §5 validation gate.
> The four extensions below close the gap between "evidence reaches the prompt" (what
> h013–h015 prove today) and what a live session would show. Spec only — not implemented.

1. **h013 / h014 / h015 end to end through the workflow** —
   `tests/integration/test_workflow_e2e_h013_h015.py`. Each case runs through the
   LangGraph workflow (`workflow/graph.py` `build_graph()`) with a deterministic mock LLM
   (canned per-case responses, same pattern as `tests/integration/test_pipeline.py`).

   *Collectors run inside the workflow, not before it.* `case_loader.py` gains an option
   to build the Kubernetes-only graph (no signals wired); the case's Prometheus / Loki /
   OTel fixtures are then served to the real collectors **from the workflow nodes**
   (`prometheus_node`, `otel_node`) — same fixture-serving shims as today
   (`_fetch_alerts`, `LokiSource._query`, a fixture `OtelBackend`), lifted out of
   `case_loader.py` so both paths share them. Otherwise `ingestion_stats` would only
   record a collector that ran on nothing. The default `build_graph()` behaviour is
   unchanged, so the existing h013–h015 tests are untouched. The same wiring is what §3
   breaks on purpose.

   *Isolation.* Collectors enabled via `monkeypatch` on `config`; a stub store (no
   embedding) injected through `config["configurable"]`; no side effects — example lookup
   disabled (`EXAMPLE_LOOKUP_DISABLED`), dry-run `kubectl` calls stubbed, no
   `ExampleStore` / DB writes.

   *Asserts on the resulting session state* (`workflow/state.py`):
   - the prompt sent by `analyze` contains the case's signal evidence — h013: the 14.4x
     burn rate and `payments-gateway`; h014: the x509 expiry log line; h015: the etcd
     compaction trace;
   - `ingestion_stats` shows the case's collector — `prometheus` (h013), `loki` (h014),
     `otel` (h015) — with `fallback: false` and a non-zero count;
   - `reasoning_history` is non-empty *because the loop really ran*: the mock answers LOW
     on the first hypothesis, then HIGH, so `archive_path` archives a path and
     `select_best` picks the HIGH one;
   - `edge_log` holds both the confidence decision and the policy decision, and a
     `verdict` is produced.

   *Frozen as the vitrine fixture.* `tools/freeze_journey_fixtures.py` runs the same
   scenario and writes `dashboard/src/sampleJourneys/h01N.json` through the API's own
   serialiser (`api/routes/sessions.py` `_state_to_response`), tagged
   `source: "fixture-replay"` so the vitrine never presents it as a live capture —
   generated by the real code, never hand-authored.

   What it does *not* prove: the real LLM's root-cause quality (still the veracity
   benchmark's job, `docs/veracity-benchmark.md`).

   > Correction (2026-10-08) to the first draft of this point. It asserted that
   > `hypothesis_sources` cite the signal evidence. They cannot today:
   > `hypothesis_sources` only carries `RemediationEngine` rule hits
   > (`workflow/nodes.py` `hypothesize_node`), and no rule reads alerts, logs or traces —
   > for h013, where Kubernetes is healthy, the list is empty. Signal evidence reaches the
   > LLM through the analyze prompt's context window, which is what is asserted instead.
   > Signal-aware rules (SLO burn, cert expiry, etcd compaction → hypothesis sources) are
   > a separate follow-up, not part of this test. It also asserted a non-empty
   > `reasoning_history` without saying how: with a mock that answers HIGH at once the
   > list stays empty, hence the LOW-then-HIGH script above.
   **Implemented (2026-10-09).** `tests/integration/workflow_harness.py` (shared
   runner: `case_loader.build_graph(case, wire_signals=False)`, collectors served by
   the fixtures from inside `prometheus_node` / `otel_node`, scripted LLM),
   `tests/integration/test_workflow_e2e_h013_h015.py` and
   `tools/freeze_journey_fixtures.py` → `dashboard/src/sampleJourneys/h013.json`,
   `h014.json`, `h015.json`. All three cases: case collector `fallback: false` with
   data (3 alerts / 10 logs / 4 traces), evidence in every analyze prompt, confidence
   edges `retry → next_path → review`, one archived path, verdict `HUMAN_REVIEW`
   (production namespace), run stopped at the human gate. Also pinned for
   reproducibility: Monte Carlo seeded, PatchTST range queries empty (synthetic
   mode), dry-run not executed. A test re-runs the tool and fails if a frozen file
   drifts from the code (timestamps aside), so the fixtures cannot be hand-edited.

   Observed while implementing, not changed here:
   - for h014 / h015 the final hypothesis comes from a Kubernetes-status rule
     (*Deployment degraded*, *Helm chart drift*), while the root cause the LLM
     writes is the signal one (cert expiry, etcd) — the gap the *signal-aware
     hypothesis rules* roadmap item addresses;
   - `log_human_decision` writes a `human → reject` edge ("no human decision
     received — defaulting to reject") *before* the interrupt, so a journey waiting
     for review already shows a reject entry in `edge_log`.
   - the scripted remediation must be reversible (`helm upgrade` / `rollout
     restart`): diagnostic-only commands have no rollback, and the policy gate
     then returns `NO_GO`, as designed.

2. **Monitoring Ops panels checked offline** —
   `tests/unit/test_monitoring_ops_metric_names.py`. Emits every self-monitoring metric
   from `telemetry.py` into an in-memory reader (HTTP server duration/active requests
   via a `TestClient` call, including one 5xx; `kubeverdict.llm.call.duration`;
   `kubeverdict.collector.fallback`), applies the OTLP → Prometheus name translation
   (`.` → `_`, unit suffix, `_total` for counters, `_bucket` for histograms) and asserts
   every metric referenced by an `expr` in
   `helm/kube-verdict/dashboards/monitoring-ops.json` is actually produced, with the
   labels the query groups by (`node`, `collector`, status code). Covers the 3 panels
   never seen with real data (error rate, LLM call duration, collector fallback rate).
   **Implemented.** The real `telemetry.instrument()` runs with the OTLP exporters
   stubbed and an `InMemoryMetricReader`; the fallback goes through the real
   `workflow.nodes._stats()`. Proves the names/labels line up — not that a real OTel
   Collector + Prometheus scrape them (that stays the single live check at the end).
3. **Degraded paths** — `tests/integration/test_collector_fallback_paths.py`. For h013,
   h014, h015, the case's own collector is made unreachable in turn. Asserts
   `ingestion_stats` flips that collector to `fallback: true`, the
   `kubeverdict.collector.fallback` counter increments for it, the signal's evidence is
   absent from the context window, and the verdict's confidence is lower than in the
   connected run (point 1) — never silently the same verdict.
   **Prerequisite landed (2026-10-07):** `otel_node` used to store a failing trace or
   log backend as `traces_fallback` / `logs_fallback` strings under one `otel` entry,
   with no `fallback: true` flag — so a dead Loki or Tempo was invisible to the B9
   overlay, to `_ingestion_failures()` and to the fallback counter, and this test could
   not have asserted anything. It now writes two entries, `otel` (traces) and `loki`
   (logs), each `{count, fallback: false}`, `{fallback: true, error}` or
   `{skipped: true}`, both through `_stats()`. Covered by the `otel_node` tests in
   `tests/unit/test_workflow_nodes_coverage.py`; `tools/b13_capture.py` reads the new
   shape. The legacy Streamlit `ui/app.py` still builds its own stats in the old shape
   (separate code path, not touched). The integration test itself is still to write.
   **Second prerequisite (found 2026-10-09): collectors swallow backend failures.**
   `PrometheusCollector._fetch_alerts`, `LokiSource._query` and the OTel backends'
   `search_error_traces` catch `requests` timeouts / errors, log a warning and return
   an empty list. No exception reaches the node, so a dead Prometheus, Loki or Tempo
   is recorded as `{alerts|logs|traces: 0, fallback: false}` — "collected, found
   nothing" — and stays invisible to the overlay, `_ingestion_failures()` and the
   fallback counter. Simulating the outage at the HTTP edge (the honest way) would make
   this test fail as written; simulating it above the collector would bypass the very
   code under test.
   Fix, backward compatible: each collector keeps returning 0 / `[]` but records the
   failure in a `last_error` attribute (reset at the start of `collect()`).
   `prometheus_node` and `otel_node` read it: when set, the entry becomes
   `{fallback: true, error, <count>}` — through `_stats()`, so the counter increments.
   A partial Loki / trace collection (some queries failed) is also `fallback: true`,
   with the count of what did arrive. Other callers (`services`, the legacy UI) see
   no change. Unit tests cover `last_error` for the three collectors and both nodes.
   *Test shape, refined:* the outage is injected at the HTTP edge (`requests.get`
   raising `ConnectionError`) for the case's collector only, the others staying on
   their fixtures. "Lower confidence" is asserted on two levels: the deterministic
   pre-LLM context score (`report.pre_llm_confidence.score`, which does not depend on
   the mock) is lower than in the connected run, and the final label — with a mock
   that answers HIGH only when the case's evidence is actually in the prompt — is
   never HIGH. The confidence decisions in `edge_log` name the failed collector
   (`ingestion failures: [...]`).
4. **Combined multi-signal case h016** — `tests/integration/cases/h016_multi_signal/` +
   `tests/integration/test_multi_signal_h016.py`. One incident where Prometheus alerts,
   Loki logs and OTel traces are all present at once, plus one decoy per signal (another
   namespace / service / uncorrelated alert). Asserts all three evidence families reach
   the context window together, each decoy is dropped, and no signal crowds another out
   of the context budget.

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
