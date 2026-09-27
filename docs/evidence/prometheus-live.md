# Prometheus & live-incident evidence (B13)

Real runs captured against a live k3s cluster by `tools/b13_capture.py` — proof the collectors work against a real endpoint, not only fixtures. Each block below is one scenario deployed, observed failing, and investigated end-to-end live.

**How to read this**

- *Snapshots, not CI baselines.* The verdict comes from a live LLM analysis plus Monte-Carlo stability sims, and the analysis prompt embeds a timestamp — so the same scenario can yield a different verdict on a later run. The `real_00N.json` files are frozen *captured* verdicts (provenance evidence), **not** deterministic fixtures, and are deliberately not wired into the B11 regression guard (which stays on the synthetic h001–h010 baseline).
- *0 alerts correlated is expected here.* `fallback=False` proves the collector reached the real Prometheus; the cluster's firing alerts are cluster-scoped (e.g. KubeProxyDown) and a fresh <2-minute incident has not tripped any `for:`-gated rule yet, so none map onto the demo-namespace entities. The proof is the live connection, not the count.

## h001_crashloopbackoff — captured 2026-06-26T02:32:18.737964+00:00

- **Cluster context:** `k3d-k0rdent` (live k3s)
- **Observed failure reason:** `Error` (real container state, not a fixture)
- **Live Prometheus alerts correlated:** 0 (`prometheus` node fallback=False)
- **Verdict:** `HUMAN_REVIEW` · risk `MEDIUM` · rollback_available `True`
- **Root cause (LLM):** The most probable root cause is that the payment-service pods are crashlooping, as indicated by the high number of restarts and the "Back-off restarting failed container" warnings. This is likely due to an issue with the container's configuration or the application itself, rather than a problem with the deployment or replica set.

## h002_imagepullbackoff — captured 2026-06-26T02:34:49.844213+00:00

- **Cluster context:** `k3d-k0rdent` (live k3s)
- **Observed failure reason:** `ErrImagePull` (real container state, not a fixture)
- **Live Prometheus alerts correlated:** 0 (`prometheus` node fallback=False)
- **Verdict:** `HUMAN_REVIEW` · risk `LOW` · rollback_available `True`
- **Root cause (LLM):** The most probable root cause is that the image "myregistry.internal/ml-models/inference:v2.4.1-gpu" is unavailable or the registry credentials are missing, as evidenced by the event "Failed to pull image "myregistry.internal/ml-models/inference:v2.4.1-gpu"" and the error message "dial tcp: lookup myregistry.internal: Try again".

## h014_cert_expiry — captured 2026-09-26T10:37:41.402469+00:00

- **Cluster context:** `rancher-desktop` (live k3s)
- **Observed failure reason:** `Running-but-NotReady (readiness probe failing)` (real container state, not a fixture)
- **Live Prometheus alerts correlated:** 0 (`prometheus` node fallback=False)
- **Live Loki logs correlated:** 20 (`otel` node logs_fallback=n/a)
- **Verdict:** `NO_GO` · risk `LOW` · rollback_available `False`
- **Root cause (LLM):** The root cause appears to be a memory issue with the billing-gateway pods. The event logs show a memory usage metric that exceeds the threshold, triggering a warning. This could be causing the pods to be in a Running but not Ready state.

## h015_etcd_compaction — captured 2026-09-26T10:52:08.985372+00:00

- **Cluster context:** `rancher-desktop` (live k3s)
- **Observed failure reason:** `Running-but-NotReady (readiness probe failing)` (real container state, not a fixture)
- **Live Prometheus alerts correlated:** 0 (`prometheus` node fallback=False)
- **Live OTel traces correlated:** 0 (`otel` node traces_fallback=n/a)
- **Verdict:** `NO_GO` · risk `LOW` · rollback_available `False`
- **Root cause (LLM):** The root cause is that the container image "busybox:1.36" is already present on the machine, preventing the pod from being fully created and ready. This is evident from the log message "Container image 'busybox:1.36' already present on machine".

## h015_etcd_compaction — captured 2026-09-26T11:02:11.826664+00:00

- **Cluster context:** `rancher-desktop` (live k3s)
- **Observed failure reason:** `Running-but-NotReady (readiness probe failing)` (real container state, not a fixture)
- **Live Prometheus alerts correlated:** 0 (`prometheus` node fallback=False)
- **Live OTel traces correlated:** 0 (`otel` node traces_fallback=n/a)
- **Verdict:** `HUMAN_REVIEW` · risk `LOW` · rollback_available `True`
- **Root cause (LLM):** The root cause is that the container image required by the inventory-api pod is already present on the machine, but it is not compatible with the desired image version. This is causing the pod to fail to start, resulting in fewer replicas being ready than desired.

## h015_etcd_compaction — captured 2026-09-26T12:16:54.296535+00:00

- **Cluster context:** `rancher-desktop` (live k3s)
- **Observed failure reason:** `Running-but-NotReady (readiness probe failing)` (real container state, not a fixture)
- **Live Prometheus alerts correlated:** 0 (`prometheus` node fallback=False)
- **Live OTel traces correlated:** 0 (`otel` node traces_fallback=n/a)
- **Verdict:** `NO_GO` · risk `LOW` · rollback_available `False`
- **Root cause (LLM):** The root cause appears to be a critical restart count for the inventory-api pod, indicating that the pod may be experiencing issues and is being restarted excessively.

## h015_etcd_compaction — captured 2026-09-26T12:30:28.560393+00:00

- **Cluster context:** `rancher-desktop` (live k3s)
- **Observed failure reason:** `Running-but-NotReady (readiness probe failing)` (real container state, not a fixture)
- **Live Prometheus alerts correlated:** 0 (`prometheus` node fallback=False)
- **Live OTel traces correlated:** 0 (`otel` node traces_fallback=n/a)
- **Verdict:** `NO_GO` · risk `LOW` · rollback_available `False`
- **Root cause (LLM):** The high restart count for the inventory-api pod suggests that the container image "python:3.11-slim" may be causing issues. The pod is restarting more than usual, which could be due to errors during the container's execution.

## h015_etcd_compaction — captured 2026-09-26T12:45:38.728778+00:00

- **Cluster context:** `rancher-desktop` (live k3s)
- **Observed failure reason:** `Running-but-NotReady (readiness probe failing)` (real container state, not a fixture)
- **Live Prometheus alerts correlated:** 0 (`prometheus` node fallback=False)
- **Live OTel traces correlated:** 40 (`otel` node traces_fallback=n/a)
- **Verdict:** `NO_GO` · risk `LOW` · rollback_available `False`
- **Root cause (LLM):** The root cause appears to be a misconfiguration in the deployment or the pod template, causing the pod to be in the Running state but not being able to transition to the Ready state. This could be due to an issue with the container image, environment variables, or resource limits.
