# Use cases

Four incidents KubeVerdict is built for. Each shows the same shape: **input → evidence used → verdict → suggested fix → human gate**. Every case maps to a validated offline fixture (`h0NN`) that runs in CI — nothing here is hypothetical.

> KubeVerdict ranks root-cause hypotheses from deterministic evidence *before* the LLM explains them, and always stops at a human approval gate. It never mutates the cluster on its own.

---

## 1. ImagePullBackOff — registry / auth / tag drift
*Fixture: `h002_imagepullbackoff`*

- **Input** — a pod stuck `ImagePullBackOff`; the query names the namespace.
- **Evidence used** — pod status + `Failed to pull image` events; the image reference declared in the Helm values vs the tag actually requested; missing `imagePullSecrets` detection.
- **Verdict** — image tag or registry auth mismatch between declared intent and the live pod spec, ranked above the generic "pod not ready" symptom.
- **Suggested fix** — `helm upgrade --set image.tag=…` or `kubectl create secret docker-registry …`, with the exact declared-vs-observed value.
- **Human gate** — the fix is proposed with a rollback; nothing is applied until you approve.

## 2. Helm values drift — declared vs deployed
*Fixture: `h012_gitops_render_vs_live` (render-vs-live) and the h001–h011 Helm-values-drift path*

- **Input** — a release misbehaving (crashloop / OOM) after a change nobody remembers.
- **Evidence used** — the **rendered** expected state (`helm template`) diffed against the live cluster (**anchor-by-render**): e.g. `spec.replicas 3→1` (critical), `resources.limits.memory 512Mi→128Mi` → OOMKilled.
- **Verdict** — the specific field that drifted from GitOps intent is the root cause, corroborated by the runtime symptom (`oom_kill`).
- **Suggested fix** — a `helm upgrade --set resources.limits.memory=512Mi …` that restores the declared value (target: a `values.yaml` patch opened as a PR — see [pr-mr-first.md](pr-mr-first.md)).
- **Human gate** — reviewed as a diff (declared → live) before anything changes.

## 3. Missing config / secret / RBAC
*Fixtures: `h004_missing_configmap`, `h005_rbac_forbidden`*

- **Input** — a pod that will not start, or a controller with `Forbidden` errors.
- **Evidence used** — dependency scan of the pod spec (referenced ConfigMaps, Secrets, PVCs, ServiceAccounts, RBAC, NetworkPolicy egress) cross-checked against what exists in the cluster.
- **Verdict** — the exact missing dependency (`missing.configmap.app-config`, `netpol.egress.blocked`, `rbac.forbidden`) rather than a vague "startup failure".
- **Suggested fix** — concrete `kubectl create configmap/secret/serviceaccount …` or an RBAC/NetworkPolicy edit.
- **Human gate** — proposed, not applied.

## 4. Probe / resource misconfiguration
*Fixtures: `h009_liveness_probe_loop`, `h010_resource_quota_exceeded`*

- **Input** — a service that keeps restarting, or pods stuck `Pending`.
- **Evidence used** — liveness/readiness probe timing declared vs observed restarts; resource requests/limits vs `ResourceQuota` usage; PatchTST temporal anomaly on memory/CPU where a live series exists.
- **Verdict** — probe timing drift (restart loop) or quota exhaustion, ranked with a confidence score; low-confidence branches are marked as dead ends and the search backtracks (visible in the Decision Journey UI).
- **Suggested fix** — adjust probe `initialDelaySeconds` / resource requests, or raise the quota — with a rollback plan.
- **Human gate** — approve or reject; production namespaces always require explicit sign-off.

---

## See it run

```bash
make demo          # a real render-vs-live RCA on h012, offline (no cluster, no Ollama)
make ui            # the Streamlit pipeline trace — pick any h0NN case
```

See also: [anchor-by-render.md](anchor-by-render.md) · [test-cases.md](test-cases.md) · [pr-mr-first.md](pr-mr-first.md).
