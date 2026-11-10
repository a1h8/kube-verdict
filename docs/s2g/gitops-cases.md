# S2-GitOps — six development cases where a GitOps manager is on the chain

> Family spec, written 2026-11-09, before any fixture or `expect.json`. Status: approved 2026-11-09; amended the same day
> after the definitions were read (last section), to approve before the fixtures.
> A development set written by us, like S2: its results measure the implementation, not generalization. Rules of
> `docs/s2-correlation-cases.md`: at least two hops, the evidence of each hop in the fixtures, decoys, tens of objects
> over two namespaces or more, a time order. Facts about Argo CD and Flux are those read in their documentation on
> 2026-11-09 (`docs/gitops-managed-by.md`). Where a behaviour is not documented, it is marked *to confirm live*, and the
> fixture writes only what the documentation states.

**What the product reads for these cases:**
- `MANAGES`, from Argo CD's `status.resources[]` or `tracking-id`, and from Flux's `status.inventory`;
- the anchors: kstatus `Stalled`, and the documented conditions. Two polarities are added to
  `ontology/spec/conditions.yaml` with these cases: Flux Kustomization `Ready` normal True, and Flux HelmRelease
  `Released` normal True. Their documentation gives the success and failure reasons;
- an Argo CD `status.health.status` anchor, normal `Healthy`, from the values listed in Argo CD's *Health* page;
- `managedFields`, for who owns a field.

## g1 — Argo CD self-heal reverts a hand fix; the workload is OOMKilled again

**Question:** "why does billing-worker keep getting OOMKilled in billing"

| Hop | Evidence in the fixtures | Joined to the next by |
|---|---|---|
| billing-worker's pods are OOMKilled | `kube/pods.yaml`: `lastState.terminated.reason: OOMKilled`, container not ready, restarts | the Deployment, by owner |
| its memory limit is 256Mi again | `kube/deployments.yaml`: `limits.memory: 256Mi`; `managedFields`: the field owned by Argo CD's manager, entry time T0+2 min | the manager that wrote it |
| an operator had raised it at T0 | `kube/events.yaml`: none; the earlier `kubectl` entry is gone from managedFields (ownership moved). Shown instead by `argocd/application.yaml` `status.operationState` of a sync at T0+2 min, `message` "successfully synced (all tasks run)", `syncPolicy.automated.selfHeal: true` | `MANAGES` (status.resources lists the Deployment) |
| Git's value is 256Mi | `argocd/application.yaml`: `status.sync.revision` = the revision with the value; `helm/manifest.yaml` (as Git renders it): `memory: 256Mi` | — |

**Cause object:** `Application/argocd/billing`. **Mechanism:** self-heal reapplies Git's 256Mi limit, so the hand fix is
reverted and the worker is OOMKilled again; the fix belongs in Git.
**Passes if** it names the Application's self-heal reverting the limit to Git's value. **Partial:** "the memory limit is
too low", without Argo CD.
**Decoys:** a past OOMKill of another worker, resolved; a Certificate renewing; an Argo CD Application of another
namespace that is OutOfSync with no effect here.
**To confirm live:** the name of Argo CD's field manager. The fixture gives it the name a live cluster shows; until then
the case is built without asserting a role for it.

## g2 — an Argo CD sync fails on an invalid resource, so a fix never reaches the cluster

**Question:** "why is checkout still failing after the fix was merged in shop"

| Hop | Evidence | Join |
|---|---|---|
| checkout's pods crash on a bad config value | `kube/pods.yaml` CrashLoopBackOff; `loki/streams.json` the parse error | the Deployment |
| the Deployment still has the old value | `kube/deployments.yaml`; `managedFields` unchanged since T0-1d | `MANAGES` |
| the Application's last sync failed | `argocd/application.yaml`: `status.operationState.phase` and `message` (the invalid resource); `status.health.status: Degraded` | `status.sync.revision` = the old revision |

**Cause object:** `Application/argocd/checkout`. **Mechanism:** its sync of the fix fails on another resource of the same
Application, so the cluster keeps the old, broken value.
**Partial:** "checkout crashes on its config", without the failed sync.
**To confirm live:** the exact values of `operationState.phase` (the CRD gives none). Until then the anchor reads
`health.status`, whose values are documented.

## g3 — a Flux Kustomization fails its build, so a fix is not applied

**Question:** "why does search still return errors in shop"

| Hop | Evidence | Join |
|---|---|---|
| search's pods log the bug | `loki/streams.json` | the Deployment |
| the Deployment is at the old revision | `kube/deployments.yaml` | `MANAGES` (Kustomization inventory) |
| the Kustomization is not Ready | `flux/kustomization.yaml`: condition `Ready` False, reason `BuildFailed`, the message; `lastAttemptedRevision` ≠ `lastAppliedRevision` | — |

**Cause object:** `Kustomization/flux-system/apps`. **Mechanism:** its build of the new revision fails, so the last
applied revision, with the bug, stays live.
**To confirm live:** whether Flux also sets `Stalled` on `BuildFailed`. The documentation lists Stalled without its
reasons, so the fixture writes `Ready` only.

## g4 — a Flux HelmRelease upgrade fails and is rolled back to the version with the bug

**Question:** "why is pricing still on the old behaviour in shop"

| Hop | Evidence | Join |
|---|---|---|
| pricing runs version 1.4.0 | `kube/deployments.yaml` image 1.4.0 | `MANAGES`, or the Helm release |
| the HelmRelease upgrade to 1.5.0 failed | `flux/helmrelease.yaml`: `Released` False `UpgradeFailed`; `Remediated` True `RollbackSucceeded`; `lastAttemptedRevision: 1.5.0` | — |

**Cause object:** `HelmRelease.helm.toolkit.fluxcd.io/shop/pricing`. **Mechanism:** the upgrade failed and was rolled
back, so the old version runs.

## g5 — two managers own the same object, and it flaps

**Question:** "why does the replica count of api keep changing in shop"

| Hop | Evidence | Join |
|---|---|---|
| api's replicas alternate | `kube/events.yaml`: `ScalingReplicaSet` up and down every few minutes | the Deployment |
| two managers list it | Argo CD `status.resources[]` and Flux `status.inventory` both list `Deployment/shop/api`; `managedFields` shows the two managers' entries, alternating times | `MANAGES` ×2 |

**Cause objects:** both managers. **Mechanism:** two GitOps tools reconcile the same object to different values.
**Passes if** it names both managers owning the Deployment.

## g0 — no incident

Applications Synced and Healthy, Kustomization Ready, HelmRelease Released. Decoys stay visible: a sync that failed
yesterday and was fixed, and an old OOMKill. **Expected outcome:** `no_incident`.

## What these cases measure, and what they cannot

- **They measure:** whether the walk reaches the manager through `MANAGES`, and whether the anchors judge it.
- **They show a known gap:** g1's cause is a manager that is healthy and synced. Its fault is to faithfully apply a bad
  value. No anchor on the Application flags it; only "the field's owner is the manager, and the value is Git's" does.
  That is the `managedFields` reading, joined to the manager, and it is the case written to measure it.
- **They cannot show:** live behaviour not in the documentation; those points are marked to confirm.

## Amendment, 2026-11-09: read through the definitions (`docs/schema-registry.md`)

**Fixtures.** Every object is checked by `tools/validate_fixtures.py` with **no omission allowed** (unlike S1 and S2):
uids, `imageID`, ownerReferences complete. The CRD objects of Argo CD and Flux are copied from the bundle into the
case's `kube/crds.yaml`, so the product reads their definitions as in a cluster. Where a value is not in a definition
or a documentation page, the fixture does not invent it: the hop is written without it, and the gap is said.

**Changes to the cases:**
- **g1.** The Application syncs with `syncOptions: [ServerSideApply=true]`, so its field manager is `argocd-controller`,
  the name Argo CD's documentation gives for server-side apply; client-side apply's manager name is not documented.
  Whether a forced apply removes `kubectl`'s ownership of the field is to confirm live; the fixture shows the state
  after it, with `argocd-controller` owning `limits.memory` at T0+2 min, and the `kubectl` entry still present for the
  fields it alone owns.
- **g2.** `status.health.status: Degraded` and `operationState.phase` are plain strings in the CRD (no enum): their
  values are declared, from Argo CD's *Health* page; `phase` is written only as `Failed` if that page or the sync
  documentation names it, else left out.
- **g3.** kstatus reads `Ready=False` as "in the process of reconciling", not as a failure. The fault is read by the
  polarity Flux documents for its `Ready` condition (normal `True`; `BuildFailed` a failure reason), declared as such.
  `lastAppliedRevision` and `lastAttemptedRevision` are in the CRD.
- **g4.** HelmRelease v2 has no `lastAppliedRevision`. The evidence is `Released=False` (`UpgradeFailed`),
  `Remediated=True` (`RollbackSucceeded`), `lastAttemptedRevision`, and `history[]` (`chartVersion`, `status`).
  `MANAGES` comes from its inventory.
- **g5.** Flux's field manager name is not documented. The evidence is the two inventories and the alternating
  `ScalingReplicaSet` events; `managedFields` shows only `argocd-controller`'s entry until a live run gives Flux's.

## Measurement rule, written 2026-11-09 before any run

- **Baseline first:** the engine as committed (`1b418bd`) runs D on g0–g5 once the fixtures are frozen. Then one
  change at a time, each with its own rule, as in `docs/s2-context-reach.md`.
- **Pass:** D's outcome names the expected cause object (g5: both managers). g0 passes with `no_incident`.
- **Guards at every change:** S1 19/19, S2 guards, g0 stays `no_incident`.
- **Predictions, before the baseline:**

| Case | Baseline prediction | Why | The change that should fix it |
|---|---|---|---|
| g1 | fail (partial at best: the OOMKill named, not the Application) | the walk reaches the Application through `MANAGES`, but it is Synced and Healthy: no anchor flags it | an anchor joining the field's owner (`managedFields`) to the manager reached by `MANAGES`, with the symptom's evidence |
| g2 | fail | Argo CD's health values are declared in no anchor yet, and the engine has no evaluator for `valid` | the `valid` evaluator and `argocd.healthy` declared |
| g3 | fail | no polarity for Flux `Ready`; kstatus does not judge `Ready=False` | Flux `Ready` polarity declared |
| g4 | fail | no polarity for `Released` | HelmRelease `Released` polarity declared |
| g5 | fail | nothing judges an object in two inventories | an anchor: an object with two `MANAGES` managers of different tools |
| g0 | pass | — | — |

A prediction that turns out wrong is recorded as wrong, with the reason.

**Order:** the fixture builder (`tests/s2g/build_family_gitops.py`), validation with no omission, `evidence.yaml`,
freeze, baseline, then the changes above in the order of the table, each committed on its own go.

## Fixtures and baseline, 2026-11-10

**Fixtures.** `tests/s2g/build_family_gitops.py` writes the six cases: 531 objects, 84 to 95 per case, over six
namespaces. `tools/validate_fixtures.py` finds **no problem and no omission** in any case. Validation found one thing
the spec had wrong: Argo CD's CRD **requires** `status.operationState.phase`, which the spec planned to leave out. Its
values come from Argo CD's notification triggers page, the only documentation that names them: `Succeeded` for a
successful sync, and `['Error', 'Failed']` together for a failed one. The fixtures use `Failed` for g2; which of the
two Argo CD writes is to confirm live. The CRD objects are not copied into the fixtures (416 KB for the
Application's alone): the registry reads them from the bundle (source 3), and reading CRD objects from a cluster
(source 2) is exercised in the live session. The graph is built by the product's collectors: discovery finds the
Argo CD and Flux kinds, and `MANAGES` is built from their inventories (2 to 12 edges per case). The cases are
frozen (`tests/s2g/frozen.json`); `tests/unit/test_s2g_cases.py` checks validity, evidence, cause objects and the freeze.

**A measurement defect found at the baseline, fixed before reading it.** The "D names the cause" check, the same as
S2's guard, looked for the object's name as a substring: "billing" was found in "billing-worker", "checkout" in
"checkout-config", so g1 and g2 read as passes. The rule is now `cause_named()`: the kind, and the name as a whole
token. Re-checked on S2's four floor cases: all four still pass, so S2's guard was not inflated. The S2 guard uses the
new rule too.

**Baseline (engine `1b418bd`), against the predictions written before:**

| Case | Prediction | Result | Right? |
|---|---|---|---|
| g1 | fail, partial at best | `oom.limit` on the pods: partial (the limit, no Argo CD) | right |
| g2 | fail: no health anchor | fail, **for another reason**: the walk stops at the ConfigMap, whose `fresh` anchor fires (the pods read its bad value at start), and does not go on to the Application through `MANAGES`. The ConfigMap is on the chain, but it is a listed decoy as the cause | outcome right, reason wrong |
| g3 | fail | `insufficient_evidence`: the log signal is seen, no rule explains it | right |
| g4 | fail | `no_incident`: no signal at all (the old version runs fine) | right |
| g5 | fail | `no_incident`: replica changes are not a fault signal | right |
| g0 | pass | `no_incident` | right |

0 of 5 named, g0 right. Two findings for the next changes:
- **g2:** an anchor that fires stops the walk. When the object it flags is managed (`MANAGES`) by a manager whose own
  state is abnormal, the cause is further up. The change for g2 is therefore not only the `valid` evaluator for Argo
  CD's health: the walk must go on past an anchored object to its manager.
- **g4 and g5 have no fault signal.** The symptom is "the old behaviour" or "replicas changing", which no signal
  extractor reads. A manager's own abnormal state (`Released=False`) is evidence, but the walk starts from signals.
  The change for them has to let a manager's abnormal state be a seed, or the question's object be one.

The guard `tests/unit/test_s2g_guard.py` holds the baseline: the floor is empty, g0 stays `no_incident`.
