# Anchors, variability and declared relations (M2): the engine reads the ontology, it holds no rule of its own

> Spec written 2026-11-09, before any of its code. Status: approved 2026-11-09. It follows the engines'
> order fixed for this work (ontology, anchors, correlation, retrieval) and replaces the hand-written parts of the
> correlation engine's minimal version.

## Why

The correlation engine's minimal version (`rca/correlation.py`, measured in `docs/s2-context-reach.md`) finds 4 of
S2's 14 causes, but with knowledge written in code:
- one function per relation (`ontology/relations.py`: `build_limits`, `build_quota_blocks`…);
- one predicate per kind (`_abnormal`: a LimitRange, a quota, a ConfigMap);
- a time rule in the signal extractor.

Each new kind or tool (ArgoCD, Flux) would add more code. The diagnostics of 2026-10-17 had already said it: the
ontology declares more than it builds (D1.2), and legitimate mutations are not modelled (D2.5).

This spec moves that knowledge into **data**, declared and versioned in the ontology:
- **relations**: which field of which kind points to which object;
- **anchors**: what a field should hold;
- **variability**: whether, and by whom, a field may change.

The engine becomes generic. It walks the declared relations and tests the declared anchors. The code keeps only a
small, fixed set of primitives.

## 1. Declared relations

`ontology/spec/relations.yaml` holds one entry per relation: its name, its source kind, its target kind, and a
**matcher** with its parameters. A matcher is one of a fixed set of primitives, written once:

| Matcher | Reads | Examples |
|---|---|---|
| `owner_reference` | `metadata.ownerReferences[]` | OWNS (any kind) |
| `label_selector` | a selector path on the source, the labels of the target (same namespace, or the namespaces a `namespaceSelector` matches) | EXPOSES (Service → Pod), RESTRICTS (NetworkPolicy → Pod), PDB → Pod, ADMITS (webhook → Namespace) |
| `name_reference` | a field path on the source holding a target's name, same namespace | CONFIGURES (`envFrom[].configMapRef.name`), SCALES (`spec.scaleTargetRef`), USES_PVC, USES_SERVICE_ACCOUNT |
| `port_match` | a Service port's `targetPort` against the selected pods' `containerPort` (number or name) | EXPOSES with ports |
| `event_quote` | a `FailedCreate` (or other documented reason) message, matched by a documented pattern, whose named group is the target's name | QUOTA_BLOCKS, LIMITS (refusal) |
| `marker` | a label or annotation that a tool writes on what it manages | MANAGED_BY: `meta.helm.sh/release-name`, `app.kubernetes.io/instance` or `argocd.argoproj.io/tracking-id` (ArgoCD), `kustomize.toolkit.fluxcd.io/name` and `helm.toolkit.fluxcd.io/name` (Flux); each one checked against its tool's documentation before it is listed |

Each entry cites the Kubernetes or tool documentation that defines the field it reads. Adding ArgoCD, Flux or a CRD
adds entries, not code. Edges keep their metadata (method, evidence, confidence, `relations_version`).

## 2. Anchors: what a field should hold

An anchor is an expectation on one field of one kind. `ontology/spec/anchors.yaml` extends the embedded schema of
`ingestion/k8s_schema.py`, whose defaults and valid values it keeps. Each anchor has a type, from a fixed set:

| Type | Says | Example |
|---|---|---|
| `declared` | the live value equals the declared one (Git, the Helm manifest) | a container image, a Service `targetPort` |
| `default` | the value is the schema's default unless declared | `spec.strategy.type` |
| `valid` | the value is one of the valid values | `spec.restartPolicy` |
| `bound` | a field stays within another field | a quota's `status.used[r] ≤ spec.hard[r]`; an HPA's `currentReplicas ≤ maxReplicas` |
| `fits` | an injected or declared resource fits what the container needs, as its termination shows | a memory limit, against `lastState.terminated.reason: OOMKilled` |
| `fresh` | a source read at start was not written after the consumer started | a ConfigMap read through `envFrom`, against the pod's start time |

**An object is abnormal when one of its anchors is violated**, and the violation is the evidence. The three
predicates of the minimal engine become anchors:
- the injected LimitRange default becomes a `fits` anchor on the limit it injected;
- the full quota becomes a `bound` anchor;
- the ConfigMap read before a change becomes a `fresh` anchor.

**Aggregation.** "Who fills the quota" is not an anchor. It is the one aggregate operator: for a violated `bound`
whose left side is a sum over the objects a relation reaches (the pods counted by a quota, by namespace and scope),
the operator attributes the excess to the root owner that holds the largest share. It is a primitive, written once.

## 3. Variability: whether a change is a drift

Each anchored field has a variability class, which decides what a difference from the anchor means:

| Class | Meaning | Source |
|---|---|---|
| `static` | must equal the anchor; a difference is a drift, and a candidate origin | default for spec fields |
| `controller:<who>` | written by a known controller; a difference is expected, **and is a hop to that controller** | documented: `spec.replicas` by an HPA (SCALES), resources by LimitRanger when the template has none (LIMITS), fields added by a mutating webhook (ADMITS) |
| `gitops-ignored` | excluded by the GitOps tool's configuration; a difference is not a drift | ArgoCD `ignoreDifferences` |
| `volatile` | changes by nature, never compared | `status.*` except fields an anchor bounds, `metadata.resourceVersion`, `managedFields` |

At run time, `metadata.managedFields` says which manager last wrote each field, and when. It confirms the class (an HPA
writing `spec.replicas`) and dates the origin of a `static` drift, for example a `kubectl set resources` by
`kubectl-set` at a given time. The history of Helm revisions says how often a field changes. A change on a field that
never changes weighs more; this is reported first, before it is ever used in a score.

## 4. The generic engine

`rca/correlation.py` is rewritten on these data, and keeps its interface:
1. From each symptom, walk the declared relations backward, at most 4 hops, each object once.
2. At each object, evaluate its anchors. A violation makes it abnormal, with the anchor as its mechanism.
3. **Variability first.** A difference on a `controller:` field is followed as a hop to its controller and is never a
   cause. A `gitops-ignored` difference is dropped.
4. The cause is the farthest abnormal object. Its origin is the write that violated the anchor, from `managedFields` or
   the Helm revision, and it must come before the symptom.
5. The score and the abstention are unchanged.

Code keeps the matchers (6), the anchor evaluators (6), the aggregate operator, and the walk. Nothing in the code names
a kind.

## 5. Measurement

- **Non-regression first.** The declared version must reproduce the minimal version before anything is added:
  - the same edges on S2 and S1, by a test that compares the graphs built before and after;
  - the floors of `tests/unit/test_s2_reach_guard.py`: 11 causes in the prompt, 4 named by D, 7/7 cases with no
    incident;
  - S1 at 19/19.
- **Then each new relation or anchor** is added as data, and measured with `tools/s2_context_reach.py` and D on S2,
  with a rule written before it, as in `docs/s2-context-reach.md`.
- **Variability's own control:** no `critical` drift on a `controller:` field. Today an HPA-managed `spec.replicas` is
  reported as a drift, which S2's fixtures had to work around.
- S2 stays the development set. The result of the engine is on S2b and the 280-case set, frozen before it runs on
  them.

## 6. Order

1. Review this spec.
2. The data format, its loader and its validation: every entry cites a source, and every matcher and anchor type is
   known.
3. The existing relations (OWNS, LIMITS, CONFIGURES, QUOTA_BLOCKS, EXPOSES, MOUNTS_CONFIGMAP) moved to data; the edge
   comparison holds.
4. The three predicates moved to anchors, and the aggregate operator; the guards hold.
5. Variability and `managedFields`; the drift control.
6. New entries, as data: SCALES, RESTRICTS, EXPOSES with ports, ADMITS; then MANAGED_BY for ArgoCD and Flux.

## Not in this spec

- Retrieval (M3) and R2: the engine verifies what retrieval proposes against these anchors, in `docs/r2-heterogeneous-retrieval.md`'s second part.
- New collectors: ArgoCD and Flux objects are read by API discovery as generic entities, and their own spec says which
  fields matter.

## Progress

- **Step 1, 2026-11-09:** the format, its loader and validation (`ontology/spec_loader.py`, 13 tests).
- **Step 2, 2026-11-09:** OWNS, CONFIGURES, QUOTA_BLOCKS and LimitRanger's refusals are built from
  `ontology/spec/relations.yaml` by the generic matchers of `ontology/declared_relations.py`, and their hand-written
  builders are removed. EXPOSES and MOUNTS_CONFIGMAP are declared too. The collector still builds them, and the declared
  path adds no duplicate.
  - **Edge comparison:** the edges of the six relations are identical on all 21 S2 cases, built before and after; the
    declared path itself built 50 OWNS, 6 CONFIGURES, 1 QUOTA_BLOCKS and 1 LIMITS edges on four of them.
  - One relation is still in code: the default LimitRanger injected. It is the variability of the pod's limits and
    moves with step 5.
  - CONFIGURES's `method` is now the generic path segment (`envFrom`, `env`). The field read is in the edge's evidence.
  - `make ci` green; the S2 guards hold.

## Amendment, 2026-11-09: anchors come from the definition first

Decided by the user before any anchor code: an anchor is detected from the resource's definition. It is not written by
hand. The sources, read on 2026-11-09:
- the Kubernetes API conventions (`kubernetes/community`, *api-conventions.md*, "Typical status properties");
- the kstatus README (`kubernetes-sigs/cli-utils`);
- the Flux *Kustomizations* page, *Conditions*;
- the Kubernetes pages *The Kubernetes API* and *Custom Resource Definitions* (publishing and defaulting).

Anchors now have three origins, used in this order:

1. **From the schema (derived).** The API server publishes an OpenAPI v3 document per group and version, and CRDs'
   structural schemas are published in it without loss, defaults included. For every kind, CRDs included, it gives
   `valid` (an `enum`), `default` and required fields. `ingestion/k8s_schema.py` (`K8sApiSchema`) already reads the API
   server's OpenAPI. It is extended to v3 and to every kind discovery finds. No anchor of this origin is written by
   hand.
2. **From the conventions (derived).** These apply to any object, without knowing its kind:
   - `metadata.generation` ≠ `status.observedGeneration`: "changes that the controller has not yet seen" (kstatus);
     the object is not reconciled;
   - kstatus conditions, "abnormal-true": `Stalled=True` is a failure, and `Reconciling=True` is in progress; `Ready`
     only as kstatus's fallback. A resource that reports them is computed generically. Flux's Kustomization
     documents it does ("compatible with the kstatus specification");
   - any other condition needs its **polarity**. The conventions say it outright: "without further knowledge of the
     conditions, it is not possible to compute a generic summary". So polarity is data: one line per condition type
     of a kind, taken from that kind's documentation (Node `Ready` positive, `MemoryPressure` negative; Deployment
     `Available` positive, `ReplicaFailure` negative…). It is declared, but it is not a rule: it says only which value
     of a documented condition is normal.
3. **Semantic (declared, minimal).** What no definition states, each entry justified by the documentation it relies on:
   - a quota's `used ≤ hard`;
   - a LimitRange limit that does not fit, against an OOMKilled;
   - a ConfigMap read at start and written after;
   - the values of a tool's own status when its schema does not give them (Argo CD's `sync.status`, whose CRD gives no
     enum, and its health, computed by its own checks).

   `ontology/spec/anchors.yaml` keeps only this third origin. Every entry says why the definition does not cover it.

**What the definition can and cannot find on S2, read from the cases' specs before any code:**
- *found from conditions with their polarity:* a1 (APIService `Available=False`), w2 (cert-manager Certificate
  `Ready=False`), and a2's symptom (HPA `ScalingLimited`);
- *found by relations, not anchors:* l2 and the quota refusals (`event_quote`), d1 (`port_match`), n1 and n2 (a selector
  that no longer matches);
- *semantic, declared:* l1 (`fits`), q1 and q2 (`bound`), c1 (`fresh`);
- *outside the cluster's objects:* d2 (Redis's own limit, in its arguments and logs) and c2 (a file's content).

So the definition finds the faults the API reports about itself, and the relations and the few semantic anchors find
the rest. The count is measured once the derivation exists, in the step that builds it, against this reading.

## Amendment, 2026-11-09: what may change is observed live

Decided by the user, before any anchor code: what a field may do, and who changes it, is first **observed on the live
cluster**. It is not declared. The origins of an anchor, in order:

1. **`metadata.managedFields`, from a single snapshot.** For each manager, it gives the fields it owns (`fieldsV1`) and
   when it last wrote them. A field owned by a controller (the HPA controller and `spec.replicas`) is
   `controller:<manager>`, and a hop to it. A field last written by `kubectl` (`kubectl-set`, `kubectl-edit`…) after the
   release is a drift, with its time as the origin. A field written by the release's manager (`helm`, Argo CD's or
   Flux's controller) at a sync is the declared state.
2. **The observed history.** Across the product's collections (the CronJob), the field values and their authors are
   kept. A field stable for a long time has its stable value as its anchor, and a change on it weighs more. A field that
   changes at every sync, or all the time, is normal when it does. This is the graph's history the diagnostics found
   missing (D1.3). It has its own spec: storage, retention, and a live measurement on its own go.
3. **The schema:** OpenAPI v3, CRDs included (amendment above).
4. **The conventions:** observedGeneration, kstatus, and the polarity of conditions (amendment above).
5. **The semantic complement**, declared and minimal (amendment above).

**Limits, stated now.**
- With no history at the first collection, origins 1, 3, 4 and 5 apply.
- A frozen fixture is one instant: managedFields can be tested on it, history cannot, except on a fixture written as
  a series of snapshots.
- A field that never changes may be wrong from the start: history says what is stable, not what is right, which is why
  origins 3 to 5 remain.

**Order of the work from here** (this replaces § 6's remaining steps):
1. the `HelmRelease` collision (`docs/gitops-managed-by.md` § 3);
2. managedFields read into the graph, and the variability it gives. The control: no drift reported on a field a
   controller owns (today an HPA-managed `spec.replicas`). *Corrected 2026-11-09:* S2 cannot measure this step. Its
   fixtures hold managedFields only on a few ConfigMaps and LimitRanges, never `fieldsV1`, and none on a Deployment.
   The step is tested on fresh fixtures, and measured on the GitOps development cases (g1 holds a
   `kubectl set resources` reverted by Argo CD).
   Read in the Server-Side Apply documentation on 2026-11-09, partly through a summary:
   - an entry's `time` is the entry's, not a field's: the last time that manager changed one of its fields. An origin
     dated from it is an upper bound;
   - manager names are not standardised ("kubectl" for kubectl's server-side apply, otherwise the client's
     User-Agent). Which manager is a controller, a person or the release's tool is a short declared table, filled from
     what a live cluster shows (the k3d check, on its own go), never from memory;
   - `fieldsV1`'s list encodings (`k:`, `v:`, `i:`) are read from the FieldsV1 reference before the parser is
     written;
3. the anchor engine on origins 3 to 5, the three predicates moved to it, and the guards holding;
4. the `inventory` matcher and the declared `MANAGED_BY` (`docs/gitops-managed-by.md`);
5. the history (origin 2), spec first;
6. the GitOps development cases and their measure.
- **Step 2 of the updated order, 2026-11-09:** `ontology/managed_fields.py` reads who owns each field from
  managedFields. The FieldsV1 encoding was read in structured-merge-diff's source. `ontology/spec/managers.yaml` lists
  a manager's role only when a source shows it: so far `kube-controller-manager` (controller) and `kubectl` (person).
  The applied-vs-live comparison uses it: a field a controller owns is no longer a drift, and is recorded as owned by
  that controller; a drift by another manager is annotated with its author, role and entry time (an upper bound for the
  origin). 3 tests on fresh fixtures, mutant killed; `make ci` green; S2 unchanged (no fieldsV1 in its fixtures).
  Still to do: the managers' roles from a live cluster, and the measure on the GitOps development cases.
