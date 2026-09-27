# Tempo & Loki — real-backend evidence (local dress rehearsal for B13/GCP+Scaleway)

Real Tempo and Loki deployed into the live `kubeverdict-obs` namespace
(context `rancher-desktop`) per `docs/local-observability-stack.md`, alongside
the already-running Prometheus/Grafana/OTel-Collector stack. This is the
connectivity proof — not a full live-incident capture (see "Not covered"
below).

**How to read this** — same convention as `docs/evidence/prometheus-live.md`:
this is provenance evidence from a real cluster, not a CI-gated regression
baseline.

## What was deployed

- `tempo` (Helm `grafana/tempo`, single-binary, local disk, 1h retention) —
  `demo/observability/tempo-values.yaml`
- `loki` (Helm `grafana/loki`, single-binary, filesystem backend) —
  `demo/observability/loki-values.yaml`
- `alloy` (Helm `grafana/alloy`, DaemonSet) — tails every pod's logs
  cluster-wide, labels `k8s_pod_name`/`k8s_namespace_name` — `demo/observability/alloy-values.yaml`
- `otel-collector` upgraded — added `otlp_grpc/tempo` exporter to the
  `traces` pipeline (was `debug`-only) — `demo/observability/otel-collector-values.yaml`
  (the chart auto-renamed the exporter type `otlp` → `otlp_grpc` on upgrade;
  functionally identical)
- `grafana` upgraded — added Tempo + Loki datasources alongside the existing
  Prometheus one — `demo/observability/grafana-values.yaml`
- `kube-verdict` upgraded — `LOKI_ENABLED=true`, `LOKI_URL=http://loki.kubeverdict-obs.svc.cluster.local:3100`,
  `OTEL_ENABLED=true`, `OTEL_BACKEND_URL=http://tempo.kubeverdict-obs.svc.cluster.local:3200`
  (`OTEL_BACKEND_TYPE` already defaults to `tempo` in `config.py`) —
  `demo/observability/kube-verdict-tempo-loki-overrides.yaml`

## Verification — captured 2026-09-26

1. **Both backends healthy** — `GET /ready` → `200` on
   `tempo.kubeverdict-obs.svc.cluster.local:3200` and
   `loki.kubeverdict-obs.svc.cluster.local:3100` (probed from an in-cluster
   pod, matching how `OtelBackend.is_available()` / kube-verdict itself would
   reach them).

2. **OTel Collector → Tempo exporter proven, not just the receiver** — sent a
   synthetic OTLP trace (`service.name=probe-service`) through
   `otel-collector-opentelemetry-collector:4318/v1/traces`; `GET
   /api/traces/{trace_id}` on Tempo returned the full trace with the correct
   resource attributes. Confirms the new `otlp_grpc/tempo` exporter actually
   persists traces — the old `debug`-only pipeline never did.
   - *Caveat:* the tag-based `/api/search?tags=service.name=probe-service`
     endpoint (what `TempoBackend.search_error_traces()` uses) returned empty
     immediately after ingest — Tempo's search index needs its WAL→block
     flush interval to elapse before a trace is tag-searchable. Direct
     trace-ID lookup (`get_trace()`) works immediately; tag search does not.
     Not a wiring bug, but means a live `h015`-style capture needs either a
     short wait after the triggering event or a tuned flush interval —
     tracked under "Not covered" below.

3. **Alloy → Loki, real pod logs, exact `LokiSource` label shape** — queried
   Loki for `{k8s_namespace_name="kubeverdict"}` and got back real
   `kube-verdict` pod log lines (e.g. `INFO: ... "GET /health HTTP/1.1" 200
   OK`) labeled with both `k8s_pod_name` and `k8s_namespace_name` — the exact
   stream labels `LokiSource`'s LogQL (`ingestion/loki_source.py`) matches
   against. Proves the evidence path would work against a real incident's
   logs, not just that Alloy is running.
   - *Side observation (unrelated to this task):* the same pod's logs
     include repeated `failed to create fsnotify watcher: too many open
     files` — a real warning worth a separate look, not investigated here.

4. **Grafana datasources registered** — `GET /api/datasources` lists
   `Prometheus`, `Tempo`, and `Loki`, all pointing at the services above.

## Not covered by this pass

- No live `h014` (cert-manager expiry) or `h015` (etcd compaction) incident
  was reproduced — this proves the pipes work, not that a real incident
  produces the same evidence shape as the fixtures. That capture (analogous
  to `tools/b13_capture.py` for `h001`/`h002` against live Prometheus) is the
  next step.
- Tempo's search-index flush latency (point 2's caveat) needs to be
  accounted for in that capture's timing.
- Retention is 1h (Tempo) with local/filesystem storage on both — fine for a
  local dress rehearsal, not for the GCP/Scaleway target
  (`docs/cloud-prerequisites.md` §3 calls for swapping in a real object-store
  backend, not new wiring logic).
