# Local observability stack — Tempo + Loki real-backend validation

Spec for the step that closes the gap found while checking the 3 dashboards
(`docs/dashboard.md`): Grafana's **Monitoring Ops** dashboard and the API were
unreachable (no Ingress, `ClusterIP` only), and **no trace/log backend
existed** — the OTel Collector's trace pipeline only had a `debug` (stdout)
exporter, and nothing shipped pod logs anywhere. This is the local,
zero-cost dress rehearsal for the GCP/Scaleway checklist in
`docs/cloud-prerequisites.md` §3 ("Observability backends — must be real, not
fixtures") — same wiring, same values-file pattern, proven here first.

## Current live cluster (before this change)

Context `rancher-desktop`, namespace `kubeverdict-obs`, all installed via Helm
on 2026-09-25:

| Release | Chart | Purpose |
|---|---|---|
| `prometheus` | `prometheus-community/prometheus` | metrics — feeds the Monitoring Ops Grafana dashboard |
| `grafana` | `grafana/grafana` | dashboards — `kubeverdict-ops` (Axis 1, B15) already loaded via the `grafana_dashboard: "1"` sidecar label |
| `otel-collector` | `open-telemetry/opentelemetry-collector` | receives OTLP from `kube-verdict`'s self-monitoring (`telemetry.py`); metrics exporter → Prometheus scrape; **traces exporter → `debug` only (not stored, not queryable)** |

Plus `kube-verdict` (namespace `kubeverdict`) with
`OTEL_SELF_MONITORING_ENABLED=true` pointed at the collector above.

**Gap:** no Tempo (trace storage), no Loki (log storage), no log-shipping
agent. `h014` (cert-expiry, Loki-sourced) and `h015` (etcd compaction,
OTel-sourced) only run against frozen fixtures (`tests/integration/cases/`) —
never against a real backend, unlike `h013`/Prometheus which already has
`docs/evidence/prometheus-live.md`. There's also no real destination for the
`jaeger` receiver protocol the collector already accepts — accepting a
protocol is not the same as having somewhere to store or query it.

## What this adds

Reuses the values files already tracked under `demo/observability/` (added in
PR #30 for a from-scratch k3d demo, namespace `observability`) — **retargeted
to the live `kubeverdict-obs` namespace** so this integrates with the running
stack instead of standing up a disconnected duplicate:

| Release (new) | Chart | Values file | Role |
|---|---|---|---|
| `tempo` | `grafana/tempo` (single-binary) | `demo/observability/tempo-values.yaml` | trace storage, local disk, 1h retention, query API on `:3200` |
| `loki` | `grafana/loki` (single-binary) | `demo/observability/loki-values.yaml` | log storage, filesystem backend, query API on `:3100` |
| `alloy` | `grafana/alloy` (DaemonSet) | `demo/observability/alloy-values.yaml` (endpoint retargeted to `kubeverdict-obs`) | tails every pod's logs cluster-wide, labels `k8s_pod_name` / `k8s_namespace_name` to match `LokiSource`'s LogQL, pushes to Loki |

Plus two **patches to existing releases** (new files, since neither release
has a values file tracked in-repo today):

- `demo/observability/otel-collector-values.yaml` — the current collector
  values (`helm get values otel-collector`) plus an `otlp/tempo` exporter
  added to the `traces` pipeline (replacing `debug`-only), so traces the
  collector receives are actually persisted, not just logged.
- `demo/observability/grafana-values.yaml` — the current grafana values plus
  Tempo and Loki datasources alongside the existing Prometheus one, so a
  human can browse traces/logs in the same Grafana instance the Monitoring
  Ops dashboard already lives in.

And a `helm upgrade` on the `kube-verdict` release itself, adding:

```yaml
config:
  lokiEnabled: "true"
  lokiUrl: "http://loki.kubeverdict-obs.svc.cluster.local:3100"
  otelEnabled: "true"
  otelBackendType: "tempo"
  otelBackendUrl: "http://tempo.kubeverdict-obs.svc.cluster.local:3200"
```

so `LokiSource` and the `TempoBackend` (`ingestion/otel_backend.py`) point at
real endpoints instead of being disabled.

## Verification (this pass)

Connectivity proof, not a full live-incident capture:

1. Tempo and Loki pods `Ready`; `/ready` responds on both (matches
   `OtelBackend.is_available()` / the Loki health check kube-verdict itself
   would use).
2. A synthetic OTLP trace sent through the collector lands in Tempo
   (`/api/search` returns it) — proves the `otlp/tempo` exporter wiring, not
   just that the receiver accepts input.
3. Alloy ships at least one running pod's logs into Loki, queryable via the
   exact LogQL shape `LokiSource` builds
   (`{k8s_pod_name="...",k8s_namespace_name="..."}`).
4. Grafana shows the new Tempo and Loki datasources and can query both.

Results recorded in `docs/evidence/tempo-loki-live.md` after execution.

## Explicitly out of scope for this pass

Reproducing the actual `h014` (cert-manager issuer expiry) or `h015` (etcd
compaction / readiness timeout) failure **live** and capturing a
`real_00N.json`, the way `tools/b13_capture.py` already does for
`h001`/`h002` against live Prometheus. That needs a real workload emitting
OTel traces and a real failure condition, not just a healthy pipe — tracked
as the next step once this connectivity layer is proven, not folded in here.

## Live incident capture — h014 / h015 (this pass)

Follow-up to the "out of scope" note above, now in progress: reproduce the
h014 and h015 failure *shapes* live and capture them with `tools/b13_capture.py`,
the same tool that already captured `h001`/`h002` against live Prometheus
(`docs/evidence/prometheus-live.md`).

**Manifests** (`demo/manifests/`, namespace `kubeverdict-demo`):

- `09-cert-expiry.yaml` (h014) — `billing-gateway` Deployment. Container logs
  the real fixture's log lines (`upstream tls handshake failed ... x509:
  certificate has expired`) on a loop; `readinessProbe` targets a port
  nothing listens on, so the pod goes **Running but never Ready** for real
  (not a crash, not a fixture) — matching `Pod.needs_telemetry`.
- `10-etcd-compaction.yaml` (h015) — `inventory-api` Deployment, same
  Running-but-NotReady mechanism, plus a Python OTel SDK sidecar-style
  container (same pattern as the existing `07-trace-emitter.yaml`) emitting
  real OTLP error spans through the collector → Tempo, `root_span:
  etcdserverpb.KV/Range`, `DeadlineExceeded` — matching the frozen fixture's
  error shape so the live capture is comparable to
  `tests/integration/cases/h015_etcd_compaction/otel/traces.json`.

**Tool changes** (`tools/b13_capture.py`):

- Generalizes the existing Prometheus-only `kubectl proxy` + apiserver
  service-proxy trick (`_PromProxy`) into a reusable proxy for any
  in-cluster service, so Loki and Tempo are reachable from the host machine
  the same way Prometheus already is — no new networking mechanism.
- Adds a `not_ready` failure mode alongside the existing container
  waiting/terminated `reason` check, since h014/h015 don't crash — they stay
  Running with `ready: false`, which the current reason-matching logic
  never detects.
- Wires `LOKI_ENABLED`/`LOKI_URL` (h014) and `OTEL_ENABLED`/`OTEL_BACKEND_URL`
  (h015) through the same proxy mechanism, and reports
  `ingestion_stats.otel.{logs,traces}` in the evidence block (both live under
  the single `otel` stats key per `workflow/nodes.py::otel_node`).

**Evidence** appended to the existing `docs/evidence/prometheus-live.md` —
kept at that path (not renamed) because `tools/roadmap.py` checks for that
exact file to mark the B13 milestone green; the file now covers all B13 live
captures, not only Prometheus ones.

## GCP / Scaleway reuse

This is the same three components (`docs/cloud-prerequisites.md` §3): once
proven here, the same `demo/observability/*-values.yaml` files apply on GKE
Autopilot / Kapsule with only the storage backend swapped (`local`/
`filesystem` → GCS/S3-compatible bucket) — no new wiring logic, matching the
project's "no custom infra code beyond values.yaml overrides per cloud" rule.
