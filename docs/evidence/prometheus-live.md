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
