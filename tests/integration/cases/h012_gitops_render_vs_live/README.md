# h012 — GitOps render-vs-live drift

The first case that validates the **anchor-by-render** wedge end to end.

Where h001–h011 diff `values.yaml` against the *stored* Helm release values,
h012 reconstructs the **expected state by rendering the chart with
`helm template`**, then diffs that rendered manifest against the **observed live
cluster**. The drift becomes RCA evidence *before* any LLM explanation.

```
chart/values.yaml         declared: replicaCount=3, limits.memory=512Mi
        │  helm template
        ▼
rendered/expected.yaml     EXPECTED state (committed as evidence)
        │  ManifestDiffer
        ▼   vs
kube/*.yaml                OBSERVED: replicas=1, memory=128Mi, Pod OOMKilled
        ▼
drift:  spec.replicas 3→1 (critical) + container.api.resources.memory 512Mi→128Mi
ranked: oom_kill = H1
fix:    helm upgrade … resources.limits.memory (restore declared intent)
```

## Layout

| Path                   | Role                                                      |
| ---------------------- | -------------------------------------------------------- |
| `chart/`               | Real Helm chart — the declared intent (source of truth)  |
| `rendered/expected.yaml` | `helm template` output, version-controlled as evidence |
| `kube/`                | Observed live cluster (drifted: replicas=1, 128Mi, OOM)  |
| `expect.json`          | Drift + ranking contract                                 |

## Validation (`tests/integration/test_render_vs_live_h012.py`)

- **Deterministic (every CI run, no helm binary):** diff the committed
  `rendered/expected.yaml` against the observed graph → assert the replica +
  memory drift and that `oom_kill` ranks H1.
- **helm-guarded:** re-render the chart with the real binary and assert it still
  equals the committed golden, so chart and evidence can't silently drift apart.

Regenerate the golden after any chart change:

```sh
helm template api ./chart --namespace production > rendered/expected.yaml
```
