# Anchor-by-render: why Kubernetes RCA needs the expected state, not only the live state

*Using Helm/GitOps rendered manifests as the evidence anchor for incident decisions.*

## The problem

When a service breaks, most Kubernetes RCA tools start from live symptoms: events, logs,
metrics, firing alerts. They reason **forward** from what the cluster is doing right now.

But the root cause is rarely where the alert fired. It is three hops away — in a misconfigured
Helm value, a `replicas` that was supposed to be `5` and is `2`, an image tag that drifted, a
resource block that a feature gate silently removed. To see *that*, you need the one thing the
live cluster cannot tell you on its own:

> **What was supposed to be running here?**

## Anchor-by-render

Anchor-by-render means using Helm/GitOps **rendered** manifests as the reference point — the
*anchor* — for incident analysis.

```
Git / Helm / Helmfile / values.yaml
        ↓  helm template (full value hierarchy)
Rendered expected manifests
        ↓
Compared against live Kubernetes state
        ↓
Declared-vs-observed drift
        ↓
Drift becomes RCA evidence
```

KubeVerdict does not only ask *what is happening in the cluster*. It asks *what should have been
running according to GitOps intent* — and treats the gap between the two as evidence that ranks
root-cause hypotheses, before any LLM explanation.

## Why render, not heuristics

A naive approach maps Helm values to Kubernetes fields by convention
(`replicaCount` → `spec.replicas`). That only works for charts following community naming.
A custom chart using `worker.replicas` or `app.instances` breaks it silently.

`helm template` is the generic ground truth. Helm itself resolves every GoTemplate conditional,
loop, feature gate (`enabled: false` → block absent) and value transformation. The rendered YAML
is exactly what *would* be deployed — no mapping logic, no per-chart assumptions.

In KubeVerdict this is two cooperating layers (see [architecture.md](architecture.md)):

- **`ManifestRenderer`** wraps `helm template --include-crds` with the Helmfile value hierarchy
  (`env value_files` < `release value_files` < inline values).
- **`ManifestDiffer`** compares rendered vs observed and emits typed drift —
  `MISSING` / `ORPHANED` / `REPLICAS` / `IMAGE` / `ENV` — as `gitops.*` annotations.
- **`AnchorEngine` (Source 2)** reuses the same render to extract exact declared field values as
  `anchor.*` drift anchors, which are indexed at ×1.6 weight so they surface above plain cluster
  entities during retrieval.

## Why this is not ArgoCD

> ArgoCD detects drift to decide whether to **reconcile**. KubeVerdict uses the same diff as RCA
> **evidence** — not as a sync trigger.

GitOps controllers answer "should I re-apply?". KubeVerdict answers "*why did this break, and
what is the safest fix?*". The drift is the same observation; the purpose is different. ArgoCD
acts on drift; KubeVerdict *explains* with it, then stops at a human-approved remediation gate.

## The LLM does not invent the diagnosis

The rendered intent, the live state, Kubernetes events, policy signals, temporal anomalies and
incident memory are assembled into an evidence path first. The LLM is constrained to *explain*
that path, not to guess from raw symptoms. Hypotheses are ranked from deterministic signals
before the model is called.

## Status (honest)

- The render-vs-live path (`ManifestRenderer` / `ManifestDiffer` / `GitopsCollector`,
  `AnchorEngine` Source 2) is **implemented and integration-tested**
  (`tests/integration/test_gitops_rca_pipeline.py`).
- It is **opt-in**: the `gitops` node activates only when a chart or `GITOPS_REPO_URL` is
  reachable. Without one, KubeVerdict falls back to the **Helm-values-drift** path
  (`HelmDriftDetector`) plus the K8s-schema anchor source.
- The currently **validated scenario set (h001–h010)** exercises that Helm-values-drift path.
- The render-vs-live path now has a dedicated validated case: **`h012_gitops_render_vs_live`**
  (`values.yaml` → `helm template` rendered manifest → live object → declared-vs-observed diff →
  OOMKilled event → evidence-ranked RCA). It is validated in two layers
  (`tests/integration/test_render_vs_live_h012.py`): a **deterministic** diff of the committed
  rendered golden (`rendered/expected.yaml`) against the observed graph, which runs on every CI
  run with no helm binary; and a **helm-guarded** freshness test that re-runs the real
  `ManifestRenderer` and asserts the chart still renders to the committed golden, so the evidence
  cannot silently rot. The render-vs-live drift detection and OOM-ranks-H1 ranking are therefore
  validated, not just claimed.

## See also

- [architecture.md](architecture.md) — GitOps diff layer, Anchor system design, why-not-heuristics.
- [test-cases.md](test-cases.md) — the validated scenario format and how to add `h0NN` cases.