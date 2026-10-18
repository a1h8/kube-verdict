# Declared, applied, live — the three states of a Helm release

> Spec, 2026-10-18. Replaces the single `GITOPS_VALUES_SOURCE` switch of `docs/fix-plan-001.md`
> H48. Written before the code; rules and controls are fixed here, before any number.

## The problem

A drift was one comparison: the chart from Git, rendered with the release's **deployed**
values (`helm get values`), against the live objects. That comparison mixes two questions and
answers neither well:

- it never sees a values change against Git (a `--set` at deploy time, a commit not yet
  synced), because the deployed values are fed into the render;
- it assumes the chart in Git is the version that was deployed — when it is not, a template
  change shows up as a live edit.

## Three states, two comparisons

```
  declared (Git: chart + its values)  ──A──▶  applied (helm get manifest)  ──B──▶  live
```

| State | Read from | Needs |
|---|---|---|
| **declared** | the chart in a configured Git repository, rendered with `helm template` and the values Git holds (the chart's `values.yaml`, a Helmfile's value files and inline values) | `GIT_REPOS_JSON` / `GITOPS_REPO_URL`, the `helm` binary |
| **applied** | `helm get manifest <release>`: the manifests Helm sent to the API server at the last install / upgrade | read access to the release Secrets (`type: helm.sh/release.v1`) |
| **live** | the objects collected from the API server | the existing collectors |

- **B — `applied → live`: edited outside Helm.** A field that differs between the manifest Helm
  applied and the live object was changed by something other than Helm (`kubectl edit`,
  `kubectl patch`, an operator). It needs no Git repository.
- **A — `declared → applied`: deployed ≠ Git.** A field that differs between Git's render and
  the applied manifest was deployed with values or a chart Git does not hold.

Both comparisons run when their inputs exist; a field may drift in A, in B or in both. There is
no mode to choose.

## Rules

1. **Provenance on every drift.** Each drift carries `provenance`: `edited-outside-helm` (B) or
   `git-vs-deployed` (A), plus the release, the object and the field path. The signal is
   `drift.config` with that attribute; the prompt shows the provenance next to the values.
2. **Same differ.** Both comparisons use `ManifestDiffer` (namespace rule of H29, values file of
   H32). Annotation keys: `applied.<field>` for B, `gitops.<field>` for A (the existing key, now
   computed against the applied manifest instead of the live object).
3. **Fields Kubernetes fills are not drifts.** Defaults the API server adds and fields
   controllers own (`status`, `metadata.managedFields`, `resourceVersion`, generated names, a
   replica count owned by an HPA) are excluded in B by the same rules as today's differ; a field
   absent from the applied manifest is not a drift.
4. **A missing input is reported, not read as "no drift".** No read access to release Secrets,
   no repository, a render that fails: the step reports `fallback: true` with the reason
   (`docs/fix-plan-001.md`, H5). With B unavailable, A still runs, and vice versa.
5. **The diagnosis uses the provenance.** `config.drift` and `oom.memory_drift` name it
   (`… was edited outside Helm: declared 512Mi in the applied release, 128Mi live`). A drift in
   A only (deployed ≠ Git, live = applied) is configuration, not an incident by itself — the
   existing rule (a drift alone stays generic) applies.

## What it replaces

- The render with deployed values (`GITOPS_VALUES_SOURCE=deployed`) is removed: B answers its
  question exactly, from what Helm applied.
- `GITOPS_VALUES_SOURCE` is removed: A always renders with Git's values.
- `HelmDriftDetector` keeps the replica and status checks it does today (`health.*`); it gains
  nothing here.

## RBAC

B reads Secrets of type `helm.sh/release.v1` only. The chart grants a namespaced Role limited to
`get` / `list` on Secrets, documented as such; it does not grant other Secrets
(`docs/fix-plan-001.md`, H45, to confirm in the live validation). Without it, B reports
`fallback: true` and the investigation goes on.

## Tests (controls that can fail)

| Test | Passes when | Control |
|---|---|---|
| live edit | live memory 128Mi, applied 512Mi → one `drift.config`, provenance `edited-outside-helm` | live = applied → no drift |
| deployed ≠ Git | applied 1Gi (from `--set`), Git 512Mi, live = applied → one drift, provenance `git-vs-deployed`, diagnosis stays generic | Git = applied → no drift |
| both | Git 512Mi, applied 1Gi, live 128Mi → two drifts, one per provenance | — |
| controller fields | live adds defaults, `status`, HPA-owned replicas → no drift in B | a changed container image → a drift |
| no Secret access | `helm get manifest` refused → B `fallback: true`, A still runs | access granted → no fallback |
| chart version | Git chart 1.3, applied 1.2 → A reports it, B unaffected | same version → A on values only |

## Replay

Read from the fixtures on 2026-10-18: in h002, h003, h009 and h024 the live objects **equal**
the deployed values (`helm/release.json`), and those differ from `helm/values.yaml` (image tag,
memory limit, liveness timeout, memory limit). Their fault is **A** (`git-vs-deployed`), not a
live edit; h012 is the **B** case (`kubectl edit` of an applied release).

To run them through the production path, each such case gains:

- `chart/` — a minimal chart whose `values.yaml` is the case's `helm/values.yaml` (the declared
  state), and `rendered/declared.yaml`, its `helm template` output, committed and checked
  against `chart/` by a helm-guarded test (as h012 does today);
- `helm/manifest.yaml` — what `helm get manifest` returns: the same chart rendered with
  `helm/release.json` (the applied state), checked the same way.

The loader then runs the production comparisons on those files and nothing else; a case leaves
its strict xfail only when A or B finds the drift with the provenance the case declares
(`expect.json["drift_provenance"]`). Each fixture change is listed in `docs/case-audit-001.md`
with what it asserts.

## Not in this spec

ArgoCD / Flux application status (an alternative source of "applied"), Kustomize without Helm,
history across several Helm revisions.
