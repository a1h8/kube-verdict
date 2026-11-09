# GitOps in the declared ontology: Argo CD, Flux and Helm as managers (M2, step 6 brought forward)

> Spec written 2026-11-09, before any of its code or cases. Status: approved 2026-11-09, as amended. It extends
> `docs/anchors-and-relations.md` with one matcher, declared relations and anchors, and specifies six development cases.
> Every field below was read in the tool's own documentation on 2026-11-09. A field marked *to confirm* is not in it,
> and is not used until a live install shows it.

## What the documentation says (read 2026-11-09)

| Tool | Object | What it records about what it manages | Source |
|---|---|---|---|
| Argo CD | `Application` (`argoproj.io/v1alpha1`), in the Argo CD namespace (`argocd` by default) | `status.resources[]`: `group`, `kind`, `namespace`, `name`, `status` (e.g. Synced, OutOfSync), `health` (e.g. Healthy, Degraded) | the Application CRD, `manifests/crds/application-crd.yaml` |
| Argo CD | the managed object itself | annotation `argocd.argoproj.io/tracking-id` (the default method), value as in the example `my-app:apps/Deployment:default/my-deployment`; or label `app.kubernetes.io/instance` = the Application's name | user guide, *Resource tracking* |
| Flux | `Kustomization` (`kustomize.toolkit.fluxcd.io/v1`) | `status.inventory.entries[].id`, format `<namespace>_<name>_<group>_<kind>`, with the version alongside | *Kustomizations* |
| Flux | `HelmRelease` (`helm.toolkit.fluxcd.io/v2`) | an inventory of the same form, *to confirm* (seen only in a summary of the page) | *HelmReleases* |
| Helm | the release | its applied manifest, read today with the release (`ingestion/helm_collector.py`) | already in the product |

**Not documented, so not used:** the labels `kustomize.toolkit.fluxcd.io/name` and `helm.toolkit.fluxcd.io/name`, and
the annotations `meta.helm.sh/release-name` and `meta.helm.sh/release-namespace`.

**State fields, documented:**
- **Argo CD:** `status.sync.status` and `status.sync.revision`; `status.health.status`, with the values Healthy,
  Suspended, Progressing, Missing, Degraded and Unknown (*Health*); `status.operationState.phase` and `.message`, the
  message "typically errors"; `status.conditions[]` with `type` and `message`. The values of `sync.status` and
  `operationState.phase` at the Application level are *to confirm*, since the CRD gives Synced and OutOfSync only for
  resources.
- **Flux Kustomization:** the condition `Ready`, with reasons `ReconciliationSucceeded`, or on failure `BuildFailed`,
  `HealthCheckFailed`, `ArtifactFailed`, `PruneFailed`, `DependencyNotReady` or `ReconciliationFailed`, and its
  message; `status.lastAppliedRevision` and `status.lastAttemptedRevision`.
- **Flux HelmRelease:** the conditions `Ready`, `Released` (False with `InstallFailed` or `UpgradeFailed`) and
  `Remediated` (`RollbackSucceeded`…); `status.lastAttemptedRevision`; `status.history[]`; remediation by
  `spec.upgrade.remediation`.

## 1. A new matcher: `inventory`

The manager declares what it manages, and the relation reads that declaration: no label is guessed.

| Parameter | Meaning |
|---|---|
| `list` | the path of the inventory on the manager: `status.resources[]` (Argo CD) or `status.inventory.entries[]` (Flux) |
| `id` | how an entry names the managed object: `fields` (`group`, `kind`, `namespace`, `name` keys, Argo CD) or `flux-id` (the `<namespace>_<name>_<group>_<kind>` string) |

`MANAGED_BY` is a new declared relation, managed object → manager, built by `inventory`. Argo CD's `tracking-id`
annotation is declared too, by `marker`: an object carrying it names its Application even when the Application is not
collected. The two must agree when both are present, and a disagreement is reported. An object in two managers'
inventories gets two edges; that is case g5.

## 2. Anchors on the managers

*Amended 2026-11-09 (`docs/anchors-and-relations.md`, "anchors come from the definition first"):* Flux's Kustomization
follows kstatus, so `flux.ready` and the not-reconciled state come from the conventions, not from a declared anchor.
HelmRelease does the same once its kstatus support is read in its documentation. Argo CD's sync and health values are
not in its CRD, so they stay declared anchors, justified as such.

| Anchor | Kind | Type | Says |
|---|---|---|---|
| `argocd.synced` | Application | `valid` on `status.sync.status` | the Application is in sync; values *to confirm* |
| `argocd.healthy` | Application | `valid` on `status.health.status`, `{Healthy}` | Degraded or Missing is abnormal, with the worst resource as the next hop |
| `argocd.operation-ok` | Application | `valid` on `status.operationState.phase`, values *to confirm* | a failed sync, with its message |
| `flux.ready` | Kustomization, HelmRelease | `valid` on the condition `Ready`, `{True}` | the reason and message are the mechanism |
| `flux.applied-is-attempted` | Kustomization, HelmRelease | `bound`: `lastAppliedRevision` equals `lastAttemptedRevision` | a revision tried and not applied: the live state is the older one |

The **origin** of a manager's fault is its revision (`status.sync.revision`, `lastAttemptedRevision`) and its time.

## 3. A collision to fix first

Flux's CRD is called `HelmRelease`, the name of our typed entity for a Helm release. Discovered, it would be taken for
it: `graph.entities(ResourceKind.HELM_RELEASE)` would return it, and code reading a typed release's fields would break,
as happened with `ResourceQuota`. **A discovered kind whose name is one of the product's own kinds is qualified by its
group** (`HelmRelease.helm.toolkit.fluxcd.io`), as `kubectl` does.

## 4. Six development cases (S2-GitOps)

Written by us, so a development set, declared as such like S2. Same rules as S2: at least two hops, evidence for each
hop, decoys, tens of objects over two namespaces or more. The scenarios below are the specs; the fixtures follow their
approval.

| Case | Chain | Cause |
|---|---|---|
| g1 | a hand fix to a Deployment, `kubectl set resources`, is reverted by Argo CD's self-heal; the workload is OOMKilled again | `Application`, whose `syncPolicy.automated.selfHeal` reapplies Git's value; the origin is the Git revision with the low limit |
| g2 | an Argo CD sync fails on an invalid resource, so a fix in Git never reaches the cluster | `Application` with `operationState` failed and its message; the live Deployment is the old one |
| g3 | a Flux `Kustomization` is `Ready=False` (`BuildFailed`), so a fix in Git is not applied | `Kustomization`; `lastAttemptedRevision` ≠ `lastAppliedRevision` |
| g4 | a Flux `HelmRelease` upgrade fails and is rolled back; the release runs the old version, which has the bug | `HelmRelease` with `Released=False` (`UpgradeFailed`) and `Remediated` (`RollbackSucceeded`) |
| g5 | an object in two managers' inventories (Argo CD and Flux) flaps between two values | both managers; the evidence is the two inventories and `managedFields` alternating |
| g0 | no incident: Applications Synced and Healthy, Kustomizations Ready; a past failed sync, resolved | `no_incident` |

## 5. Measurement

- Each piece first passes its own tests on fresh fixtures, with mutants: the matcher, the relation, the anchors and the
  collision.
- The S2 guards hold, and S1 stays 19/19.
- On S2-GitOps (development): D names the expected cause, with a rule written before each change, as in
  `docs/s2-context-reach.md`.
- A live check on k3d with Argo CD and Flux installed, on its own go, confirms the fields marked *to confirm*.

## 6. Order

1. Review this spec.
2. The collision (§ 3), with a test.
3. The `inventory` matcher and the declared `MANAGED_BY` (§ 1), with tests.
4. The anchors (§ 2), once the M2 anchor engine exists (step 3 of `docs/anchors-and-relations.md`).
5. The six cases' fixtures, their freeze, then the measure.

## Implementation note, 2026-11-09, before the code

- **Direction.** The relation is named `MANAGES`, from the manager to the managed object (Application → Deployment),
  so that the correlation walk, which goes back to what acts on an object, reaches the manager. `MANAGED_BY` above
  means the same edge read the other way.
- **Matchers.** Two:
  - `inventory`, with `list` and `id: fields | flux-id`;
  - `marker`, for Argo CD's `argocd.argoproj.io/tracking-id`, whose value starts with the Application's name before
    the first `:`, as in the documented example. With the annotation, the Application is looked up by name in any
    namespace, since the value does not carry the Application's namespace.
- **Rule.** These relations add edges only where an Argo CD or Flux object exists, and S2 has none. So S2's chains,
  outcomes and guards must not change. Tested on fresh fixtures, mutants included. Measured on the GitOps development
  cases.
- **Done, 2026-11-09.** `MANAGES` is declared three times in `ontology/spec/relations.yaml`:
  - Argo CD's `status.resources[]`, by `inventory`;
  - the `tracking-id` annotation, by `marker`;
  - Flux's `status.inventory.entries[]`, by `inventory`.

  The correlation walk follows it. A YAML trap was found on the way: the key `on` reads as a boolean, so `marker`'s
  parameter is `store`. 5 tests on fresh fixtures, mutants killed (a Flux id read in the wrong order; an entry of
  another kind matching a same-named object). S2 unchanged, guards hold, `make ci` green.
