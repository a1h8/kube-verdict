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
- **g2:** the ConfigMap's `fresh` anchor fires and its chain is reported. *Corrected the same day, on reading
  `rca/correlation.py`:* the walk does not stop there; it goes on to the Application over `MANAGES`, where no anchor
  judges it. Two things follow: the Application needs its anchor (Argo CD's health), and when both chains exist, the
  report lists every chain's cause (`Outcome.root_cause` joins them), shortest first, so the ConfigMap would still
  come first.
- **g4 and g5 have no fault signal.** The symptom is "the old behaviour" or "replicas changing", which no signal
  extractor reads. A manager's own abnormal state (`Released=False`) is evidence, but the walk starts from signals.
  The change for them has to let a manager's abnormal state be a seed, or the question's object be one.

The guard `tests/unit/test_s2g_guard.py` holds the baseline: the floor is empty, g0 stays `no_incident`.

## Change 1 (g1), rule written 2026-11-10 before its code

**What is missing.** The walk reaches `Application/argocd/billing` from the Deployment over `MANAGES`. Nothing judges
it: it is Synced and Healthy. Its fault is that the field the symptom points at, the memory limit of an OOMKilled
container, is written by its own field manager.

**The change, as data and one generic evaluator:**
- `ontology/spec/managers.yaml`: `argocd-controller`, role `release`, `for_kind: Application`, sourced from Argo CD's
  *Sync Options* page ("Argo CD's server-side apply manager (`argocd-controller`)"). Flux's managers are added when a
  live run shows their names.
- `ontology/spec/anchors.yaml`: `gitops.applied-limit-fits`, kind `Application`, type `fits`, params
  `through: MANAGES`, `resource: spec.template.spec.containers[].resources.limits.memory`,
  `evidence: lastState.terminated.reason=OOMKilled`.
- `ontology/anchors.py`: `fits` reads `through` when given: reached over that relation from a managed object whose
  `managedFields` say the manager `for_kind` of the reached kind owns `managed_field`, while a pod the managed object
  owns is OOMKilled. The existing LimitRange path is unchanged.

**Pass rule, sharpened.** A case passes when `cause_named()` holds for the **first** chain's cause, not for any of the
joined causes, so that a report naming a decoy first does not pass. Applied from this change to S2-GitOps only; S2's
guard is checked under it too, and reported.

**Predictions:** g1 passes. g0–g5 otherwise unchanged (no other case has an OOMKilled pod under an Application whose
manager owns the limit; g0's old OOMKill is on a container that is ready and running, which is no signal, and its
Deployment has no managedFields). S1 19/19 and the S2 guards hold: no S1 or S2 case has an Application.

**Change 1, result (2026-11-10).** g1 passes: D's first diagnosis is `Application/argocd/billing`, "Deployment/billing/
billing-worker spec.template.spec.containers[].resources.limits.memory is written by argocd-controller for
Application/argocd/billing (managedFields, 2026-11-02 09:02); 2 of its pods are OOMKilled". g0, g2–g5 unchanged, as
predicted. S2 unchanged: the same four cases named, also under the first-chain rule, and its seven no-incident cases
keep `no_incident`. S1 19/19 (`make ci`). By the rule, kept. One bug of the change itself found on the frozen case
before it passed: the typed Deployment's raw object is in the client's snake_case (`api_version`), so its group was
not read; fixed by reading both spellings, as the rest of the engine does. 6 tests on fresh fixtures, 4 mutants killed
(the relation, the manager's kind, the OOMKill, the pods' depth). `spec_paths` reads a `fits` anchor's paths on the
hop's target kind; with `*`, the path is listed as unchecked.

## Change 2 (g2), rule written 2026-11-11 before its code

**What is missing, two things.**
1. *Nothing judges the Application.* Its last sync failed (`status.operationState.phase: Failed`, with the invalid
   Ingress in `operationState.message`), but no anchor reads that field.
2. *Even judged, it would not come first.* `rca/correlation.py` walks on past an anchored object and sorts the chains
   by length. The Application is reached from the ConfigMap over `MANAGES`, so its chain is the ConfigMap's chain
   plus one hop, and the ConfigMap, a listed decoy, stays first. Under the first-diagnosis rule of change 1, the anchor
   alone leaves g2 failing.

**What the definitions give.** Read from the bundle's Application CRD (Argo CD v3.6.1), not from memory:
`status.operationState.phase` and `status.health.status` are both `type: string`, **no `enum`**. A `valid` anchor
would normally come from a schema enum (`docs/anchors-and-relations.md`, amendment of 2026-11-09, origin 1). Here it cannot, so the values
are declared by hand, as the directive allows for what a definition does not contain, with their source: Argo CD's
*notification triggers* page names `Running` (`on-sync-running`), `Succeeded` (`on-sync-succeeded`), and `Error`,
`Failed` (`on-sync-failed`). Nothing else documents the phase. `health.status` is not used: g1's Application is
`Degraded` because of the OOMKills, so health does not separate a failed sync from a failing workload.

**The change, as data, one generic evaluator, and one ordering rule:**
- `ontology/spec/anchors.yaml`: `argocd.sync-phase-valid`, kind `Application`, type `valid`, params
  `field: status.operationState.phase`, `values: [Running, Succeeded]`, `message: status.operationState.message`;
  variability `controller:argocd` (corrected below: `volatile`); source the triggers page. A phase outside the list fires; an absent phase does not
  (no claim without the field).
- `ontology/anchors.py`: the `valid` evaluator, generic: read `field` on the reached object; if present and not in
  `values`, the mechanism is `<field>=<value>, not one of <values>`, followed by `message` when that path has a value.
  No kind or group in the code. `spec_paths` checks `field` and `message` against the Application CRD.
- `rca/correlation.py`, **ordering**: a chain whose hops pass through another chain's cause object is listed before
  that chain (its cause is upstream: it explains why the other cause stays). Otherwise the order is unchanged
  (length, then name). No kind in the rule.

**Pass rule:** unchanged from change 1 (the first diagnosis names the cause).

**Predictions:**
- g2 passes: first diagnosis `Application/argocd/checkout`, the mechanism quoting the Ingress `Unsupported value`.
  The ConfigMap's chain stays in the report, second.
- g0, g1, g3, g4, g5 unchanged. g0 and g1's Applications are `Succeeded` or have no phase; g3 and g4 have no
  Application; g5's is `Succeeded`.
- S1 19/19 and S2 unchanged: no S1 or S2 case has an Application, so the anchor cannot fire. The **ordering rule** can
  touch them, though: the prediction is that no S2 chain passes through another chain's cause, so S2's four named cases
  stay first. Any S2 or S1 case whose first diagnosis changes is listed, named or not.

**Not kept if:** g2 does not pass; any guard moves (S1 below 19/19, an S2 named case no longer first, g0 not
`no_incident`, g1 no longer first); or the ordering rule changes an S2 first diagnosis for the worse. A change of the
ordering rule that helps nowhere but g2 is reported as such.

**To confirm live:** whether Argo CD writes `Error` or `Failed` for this failure (both are outside the valid list, so
the anchor fires on either); whether a phase not on the triggers page (e.g. `Terminating`) exists and would be a false
alarm.

*Correction, 2026-11-11, before the measurement, on reading the page.* The values are right, the attribution was not.
The catalog's triggers (`on-sync-running`, `on-sync-succeeded`, `on-sync-failed`) are listed there without their
`when` expressions. The phase values are named on the *triggers* page
(`operator-manual/notifications/triggers/`), in its `sync-operation-change` example: `phase in ['Succeeded']`,
`in ['Running']`, `in ['Error', 'Failed']`; `on-deployed` also reads `phase in ['Succeeded']`. Neither page names
another phase (no `Terminating`). The anchor cites the triggers page.
The variability is `volatile`, not `controller:argocd`: the loader requires a `controller:` variability to name the
hop to its controller, and here Argo CD writes the status of its own object; a status field that changes with every
sync is `volatile` in the fixed set.

**Change 2, result (2026-11-11), not yet kept.** First diagnoses compared case by case with a run just before the
change (S1, S2, S2-GitOps):
- g2 is named first: `Application/argocd/checkout`, "status.operationState.phase=Failed, not one of ['Running',
  'Succeeded']: one or more objects failed to apply, reason: Ingress.networking.k8s.io "checkout" is invalid: …
  Unsupported value: "Prefixx" …". The ConfigMap's chain is second.
- No other first diagnosis moved: S1 (19 cases), S2 (the same four named first), g0, g1, g3–g5.

**Two predictions were wrong, and the pass is not earned by the mechanism the rule gave:**
1. *"The anchor alone leaves g2 failing" is refuted.* With the ordering rule switched off, g2 still has the
   Application first. Its chain does not go through the ConfigMap: the walk reaches the Application in **one hop from
   the Deployment** (`Deployment/shop/checkout <-MANAGES- Application/argocd/checkout`), and the ConfigMap's chain is one
   hop too (`Pod <-CONFIGURES- ConfigMap`). The baseline's reading "the Application is one hop further than the
   ConfigMap" was wrong.
2. *The ordering rule does nothing.* No chain in S1, S2 or S2-GitOps passes through another chain's cause, g2 included.
   With two chains of equal length, the order is the sort's second key, **the cause's name**: `Application/…` sorts
   before `ConfigMap/…`. g2 passes on the alphabetical order of the kinds. That is not a mechanism; a decoy whose kind
   sorts before `Application` would take first place.

Observed while looking, not measured: the graph holds `Application/argocd/checkout MANAGES ConfigMap/shop/checkout-config`.
A tie-break that reads the graph (when one chain's cause has a typed relation to another chain's cause, it is upstream)
would put the Application first for a reason. It would be a new rule, written before its own measurement.

## Change 2b: the engine's spec, points 4 and 5, rule written 2026-11-11 before its code

Decided with the user after change 2's result: the ordering rule of change 2 is withdrawn (it changed no case), and
the tie is broken by what `docs/correlation-engine.md` § 4 already specified and the code never did:
- **point 4**, "the cause is the farthest abnormal object of the chain";
- **point 5**, chains ranked by score, then length, then fqn.

**What the code does today.** `rca/correlation.py` records a chain for every abnormal object the walk reaches, keyed by
that object, so an abnormal object part-way up a path is reported as a cause of its own. Every chain gets the same
score (`CHAIN_SCORE` in `rca/diagnosis.py`); ranking is length, then name.

**The change.**
- *Point 4.* A chain is a path from a symptom as far as the walk goes (`MAX_HOPS` unchanged). Its cause is the
  **farthest** abnormal object on it; an abnormal object nearer the symptom stays on the chain as a hop, its mechanism
  appended to that hop's evidence, and is no longer a cause of its own **on that path**. It remains a cause if some
  other path from a symptom has it as its farthest abnormal object. Per cause, the shortest chain is kept, as today.
- *Point 5, the part with inputs.* A chain's score is 0.5 × the product of its hops' edge `confidence` (each edge
  carries one, `ontology/relations.py`). Ranking: score, then length, then fqn. **Not implemented, said here:** "+ 0.15
  per hop confirmed by an independent source" and "− 0.3 per contradiction". No hop carries a second source, and
  nothing marks a hop's target as healthy now; both need inputs the graph does not have. Not invented.
- The chain's score orders the chains only. `Diagnosis.score` stays `CHAIN_SCORE`, so nothing downstream (the gate,
  `REPORT_MIN`) moves.

**Control that can fail** (unit test, fresh fixtures, not a frozen case): `Pod <-CONFIGURES- X <-MANAGES- Application`,
with X abnormal and a kind that sorts before `Application` (`AAAConfig`, a generic entity with a `fresh`-like anchor
declared for the test). Today X is a 1-hop chain and comes first. After the change, the Application is the only cause
on that path, with X as a hop. A test that passes on the code of change 2 is no control; this one must fail on it.

**Predictions.**
- g2: first diagnosis `Application/argocd/checkout`, now for point 4's reason: the path `Pod <-CONFIGURES- ConfigMap
  <-MANAGES- Application` makes the ConfigMap a hop. **The ConfigMap is no longer a cause in the report**: its only
  path from a symptom continues to the Application. It still shows in that path's hops.
- g1: unchanged, first `Application/argocd/billing`.
- g0, g3, g4, g5: unchanged.
- S1 19/19. S2: the four named cases stay first. Not certain: point 4 changes every case where an abnormal object
  lies upstream of another one; S2 holds chains through LimitRanges, quotas and ConfigMaps, and the scores now differ
  by edge confidence. Every first diagnosis that moves is listed.

**Kept only if:** the control fails on change 2's code and passes after; g2 passes; no guard moves (S1 19/19, S2's
four still first, g0 `no_incident`, g1 first). Otherwise not kept, and the result is written anyway.

**Change 2b, result (2026-11-12).** First diagnoses compared case by case with the run made before change 2:
- g2 is named first, `Application/argocd/checkout`, and is now the only chain: the ConfigMap is a hop of a path whose
  farthest abnormal object is the Application, and no path from a symptom ends at it.
- No other first diagnosis moved: S1 (19 cases, `make ci` 3 625 passed), S2 (the same four named first), g0
  (`no_incident`), g1, g3, g4, g5.
- **The control:** the fresh fixture `Pod <-CONFIGURES- AAAConfig <-MANAGES- Application` fails on the code before
  the change (4 of 5 tests; the fifth, a nearer object that stays the cause when nothing abnormal is above it, holds
  on both, as it should) and passes after. On g2 itself, with the name tie-break reversed, the Application stays first.
- **One prediction wrong in detail:** "the ConfigMap still shows in that path's hops". Per cause, the shortest chain
  is kept, and the Application's shortest is the one-hop path from the Deployment. The ConfigMap that holds the bad
  value is no longer in the report at all. The answer is right; it says less. Keeping every intermediate abnormal
  object of a cause's paths is a possible later change, with its own rule.
- Tests: 5 for the walk and the rank, 4 for the `valid` evaluator; 7 mutants killed (nearest cause instead of
  farthest; no mechanism on the hop; score without confidence; rank without score; a chain emitted at every prefix;
  an absent field that fires; no quoted message). The S2-GitOps guard's floor is g1 and g2.
- **Point 5, still open:** the independent-source bonus (+0.15) is the place of hybrid retrieval in the engine
  (`docs/r2-heterogeneous-retrieval.md` § Roles: retrieval proposes, the engine verifies). It is change 3, spec first,
  measured on R2: S2-GitOps has no document corpus and cannot test it.

By the rule, kept.
