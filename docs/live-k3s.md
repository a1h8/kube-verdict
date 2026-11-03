# Live on K3s — the single cluster for all live work

> Decided 2026-10-14: all live work moves to K3s, on the existing `k3d-dev` cluster
> (K3s in Docker). Inventory read from the cluster on 2026-10-14, read-only. This file
> replaces the live assumptions of `docs/local-observability-stack.md` (written for a
> `rancher-desktop` context that no longer exists). Nothing below has been applied yet:
> every step that changes the cluster needs an explicit go.

## Why this cluster is enough, and where it stops

Enough for the **first live proof**: a real Kubernetes API and kubelet, so OOMKills,
CrashLoopBackOffs, events and image pulls are real incidents, not fixtures; Helm,
Prometheus, Ollama in-cluster; Loki / Tempo / Grafana and ArgoCD or Flux can be added.

Not enough for the **last proof**: a single node (no node scenarios until agents are
added with `k3d node create`), a prepared environment (external credibility will need a
real cloud and incidents we did not write), Docker networking quirks.

## Inventory — `k3d-dev`, 2026-10-14

| Element | State | Where / version | Notes |
|---|---|---|---|
| Cluster | ✅ | k3d `dev`, 1 server / 0 agents, K3s v1.35.5+k3s1 | 8 CPU, 61 GiB, StorageClass `local-path`, Traefik |
| metrics-server | ✅ | `kube-system` | feeds the metrics collector |
| Ollama | ✅ | `kubeverdict/ollama`, image pinned by digest (0.40.8, `docs/live-validation-001.md` step 1), models `mistral:latest` and `qwen2.5:3b-instruct`, PVC 10 Gi | limit 16 Gi since 2026-10-30: 8 Gi was OOM-killed under the replay (2026-10-13), 8 Gi and 12 Gi by the tokenizer measurement (CPI-27) |
| kube-verdict | ⚠️ partial | `kubeverdict/kubeverdict` **CronJob**, hourly, `python main.py --query "unhealthy pods, drift, crashloops, pending PVCs"`, image `ghcr.io/a1h8/kube-verdict:latest` | **unpinned image**; no API, no dashboard, results only in job logs; it calls the same Ollama every hour |
| Prometheus | ✅ | Helm `prometheus-29.35.0` (v3.15.0), namespace **`observability`**, `prometheus-server` ClusterIP | no SLO alert rules for h013 yet |
| Loki, Tempo, Grafana, Alloy, OTel Collector | ❌ | — | values exist in `demo/observability/` (namespace `observability`) |
| ArgoCD / Flux | ❌ | — | needed for GitOps incidents |
| PatchTST stack | ✅ | namespace `patchtst`: Flink (jobmanager + 2 taskmanagers), Beam job server, Mimir distributed 5.5.1, Redpanda, SeaweedFS, `kb` | the push path to kube-verdict needs the kube-verdict API, which is not deployed |
| Demo scenarios | ✅ files | `demo/manifests/00…10`, namespace `kubeverdict-demo` | not applied |

## What in the repo still points at the old cluster

- `tools/b13_capture.py`: `PROM_NS = OBS_NS = "kubeverdict-obs"` (the old
  `rancher-desktop` stack). On `k3d-dev`, Prometheus is in **`observability`**. Usage
  examples name `k3d-k0rdent` and `rancher-desktop`.
- `docs/local-observability-stack.md`: describes `rancher-desktop` / `kubeverdict-obs`.
- `docs/evidence/*.md`: past captures on those clusters (history, kept as is).

## Scenario feasibility on `k3d-dev`

| Level | Needs | Scenarios |
|---|---|---|
| **L1** | what is there (Kubernetes, Prometheus, Ollama) | h001 crashloop (`01`), h002 image pull (`04`), h003 OOM (`02`), h004 missing config (`03`), h022 healthy (`06`) |
| **L2** | + Loki, Tempo, Grafana, Alloy, OTel Collector | h014 cert expiry (`09`), h015 etcd compaction (`10`), traces (`07`), h016 (no manifest yet) |
| **L3** | + ArgoCD or Flux | GitOps drift incidents: `kubectl edit` hotfix not in Git, forgotten values override, partial sync |
| **L4** | + agent nodes | node pressure, noisy neighbour, PDB-blocked drain (h021, h025, M5, M7) |
| **L5** | a real cloud (Scaleway, then GCP) | the external validation of `docs/cloud-prerequisites.md` §5 |

h005–h013 and h024 have fixtures but no `demo/manifests` file yet.

## Risks to handle during live runs

- **One Ollama for everything.** The hourly CronJob, a replay and a live capture compete
  for the same CPU-only model, and it already ran out of memory once. A live session runs
  alone: the CronJob is suspended for its duration, and no replay runs at the same time.
- **Unpinned images** (`kube-verdict:latest`, `ollama:latest`): each live record names
  the image digests it ran with, otherwise it cannot be reproduced.
- **Port-forward fragility** (a dead forward held a request for 21 h): live capture goes
  through the API server's service proxy, as `b13_capture.py` already does for
  Prometheus.
- **Single node:** L4 scenarios are not attempted before agents exist.

## L1 — the first live session (proposed, not started)

1. Retarget `tools/b13_capture.py` to `k3d-dev`: Prometheus namespace `observability`,
   given as a flag instead of a constant. Small change, needed, no new feature.
2. With a go: suspend the CronJob, apply `00-namespace.yaml` + one scenario (`02-oom.yaml`
   first: the OOM is real, and `oom_kill` now fires on a live graph), let
   `b13_capture.py` wait for the real failure, investigate with the real Prometheus and
   mistral, grade, freeze, tear down, resume the CronJob.
3. Record: the graded `tests/veracity/real_00N.json`, the verdict triple, Ollama and
   kube-verdict image digests, the evidence block.

**Acceptance for L1:** one live incident on K3s, investigated end to end, graded, with
its run reproducible from the record.
