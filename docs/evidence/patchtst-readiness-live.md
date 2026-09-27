# PatchTST readiness — live evidence

Real runs against the live `rancher-desktop` cluster, proving the
[patchtst-readiness.md](../patchtst-readiness.md) checklist — provenance
evidence, not a CI-gated regression baseline (same convention as
`docs/evidence/prometheus-live.md` / `tempo-loki-live.md`).

## 1. Webhook push — patchtst namespace → kube-verdict namespace

Sent from a throwaway pod inside `patchtst`:

```json
{"alerts":[{"entity_uid":"Deployment/patchtst-demo/billing-gateway",
  "metric_name":"ready_ratio","ts":<epoch_ms>,"severity":"critical",
  "score":8.4,"method":"patchtst","horizon":"short","n_points":60,
  "labels":{"namespace":"patchtst-demo","deployment":"billing-gateway"},
  "text":"PatchTST readiness live-proof signal"}]}
```

to `http://kube-verdict-kubeverdict.kubeverdict.svc.cluster.local:8000/api/v1/webhook/signal`
(no auth token — `KUBEVERDICT_API_TOKEN` unset in this deployment, so
`require_token` is a no-op, confirmed by reading `api/auth.py` first).

- **Response:** `HTTP 202`, `{"session_ids":["2a6faf55-9964-4e91-a4ca-74fc8aa21c67"],"skipped":0}`
- **kube-verdict's own log:** `webhook: started session 2a6faf55-... for signal Deployment/patchtst-demo/billing-gateway/ready_ratio`

Confirms the full contract round-trip: PatchTST-shaped payload → kube-verdict
webhook → correctly-scoped session, cross-namespace, in the same cluster
PatchTST would actually run in.

## 2. KB store against live MinIO

`patchtst-kb` bucket (`mimir-minio`, in-cluster) already contains 46 real
`.parquet` files under `kb-flink/`, dated 2026-09-22/23 — a prior successful
run wrote them. Confirms `kb/store.py`'s `s3://...?endpoint_override=...`
resolution works against a real S3-compatible store, not just the
`file://` path the unit tests cover.

## 3. Flink live run

```
kubectl apply -f deploy/flink/50-submit-mimir.example.yaml
```

Driver pod (`flink-submit-mimir-nh7qc`) ran preflight OK, submitted to the
Beam Job Server, which translated and submitted to the real Flink cluster:

```
Job 'BeamApp-root-0926123742-33712cea' (35815b190e2c196580034701b9c9a39c)
  state: RUNNING → FINISHED
  tasks: 2 total, 2 finished, 0 failed
  checkpoint storage: s3://patchtst-flink/checkpoints (RocksDB backend)
```

One transient error logged on a TaskManager before the job reached `RUNNING`:

```
java.io.FileNotFoundException: /tmp/beam-artifact-staging/.../submission_environment_dependencies.txt
```

Self-recovered — the same task later logged `switched from RUNNING to
FINISHED` cleanly. Not re-triggered; flagged in the readiness doc as worth
watching if it recurs, not treated as a blocker here.

**Caveat:** this run's zscore detector found no anomaly in its live
`sim_.+` window (checked via `mc find local/patchtst-kb --newer-than 1h` —
no new object), so it did not itself add a new KB row. The KB-write proof in
§2 and the clean-execution proof in §3 are both real but come from different
runs, not one atomic capture. A rerun during a genuine anomaly window would
close that gap.

## Not covered by this pass

- Dataflow (GCP) equivalent — no GCP project provisioned yet, per
  `docs/cloud-prerequisites.md`.
- Killing the kube-verdict endpoint mid-flight to watch `kb/alert.py`'s
  best-effort path live (verified by code + unit test instead — see
  `tests/test_kubeverdict_alert.py` in PatchTST).
- The two uncommitted fixes on `feat/flink-local-live`
  (`00-flink-conf.yaml`, `30-job-server.yaml`) that this run's cluster state
  already depends on — still need to land as commits.
