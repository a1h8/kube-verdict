# Correlation engine — from a symptom to its cause, hop by hop

> Spec 2026-11-08, not implemented. Review before any code. It follows `docs/structured-diagnosis.md`, whose
> principle is unchanged: deterministic, replayable, versioned, with the LLM optional and limited to wording.
> Why: `docs/s2-results.md`. The structured diagnosis (D) found 0 of the 14 causes of S2.

## Why

S2's records show where D stops, case by case. The `unclassified` counts are those D wrote in each record.

| Gap | Cases | What the record shows |
|---|---|---|
| **G1, vocabulary.** The fault is never typed as a signal. | a1, l2, w2 | Each event is left unclassified (1, 1 and 3 events), so D answers "no incident": a webhook that fails closed, a LimitRange refusing a pod, a custom metrics API that does not answer. |
| **G2, relations.** The signals are typed, but nothing links them to an object two hops away. | a2, c1, c2, d1, n1, n2 | D answers "insufficient evidence". The graph has no edge from a NetworkPolicy of another namespace, from a ConfigMap used through `envFrom`, from a Service port, or from an HPA's behaviour to the pods they act on. |
| **G3, time.** A fault already over is read as current. | q0, q1, q2 | An OOMKill 5 h before the symptom, on a container that is ready now, is `container.oom_killed` and wins over `quota.exceeded`. |
| **G4, first hop only.** A rule names the object where the symptom shows. | l1, w1, d2 | The OOMKill without the LimitRange that set its limit; the failed pull without the webhook that injected the image; a replica drift instead of the full cache. |

The rules of `rca/diagnosis.py` are one function per family, and each reads signals on the symptom's own object.
Adding one more such rule per S2 case would fit S2 and nothing else. This spec makes the cause a **chain over
relations Kubernetes defines**, the same for every family.

## Principle

```
signals (typed) ──► symptoms ──► walk the relations back from each symptom ──► chains ──► ranked diagnoses
                                  │                                            │
                                  └ each hop: a relation and its evidence       └ cause = the last abnormal object;
                                                                                  origin = the change that made it so
```

A **chain** is a list of hops. Each hop holds:
- the source object and the target object (fqn);
- the **relation** (the catalogue below);
- the **evidence** that the relation holds (spec fields read on both objects);
- the **evidence** that the target is abnormal (a signal, or a check on its state);
- the method and its version.

The answer prints the hops in order, so an analyst can check each one against the raw objects, as
`tests/s2/evidence.yaml` does for the cases.

## 1. Relations, read from the Kubernetes API semantics

Each relation is computed from the fields the Kubernetes API documents, never from names or text. Most exist in
`ontology/relationships.py` but are not built today, or are built too narrowly.

| Relation | From → to | How it is computed | Today |
|---|---|---|---|
| `OWNS` | Deployment → ReplicaSet → Pod; CronJob → Job → Pod | `metadata.ownerReferences` | built (`SPAWNED_BY` declared for Job → CronJob) |
| `EXPOSES` | Service → Pod | `spec.selector` against the pod's labels, same namespace; then **each `spec.ports[].targetPort` against the pod's `containerPort` (number or name)** | built, without the port check |
| `CONFIGURES` | ConfigMap or Secret → Pod | `volumes[].configMap`, `envFrom[].configMapRef`, `env[].valueFrom.configMapKeyRef` (and their Secret forms) | volumes only (`MOUNTS_CONFIGMAP`) |
| `RESTRICTS` | NetworkPolicy → Pod | `spec.podSelector` on the pods of the policy's namespace, and the peers of each ingress or egress rule: `podSelector` and `namespaceSelector` against the labels of **pods and namespaces anywhere** | declared, not built; podSelector checked as an annotation in the pod's own namespace |
| `LIMITS` | LimitRange → container | `spec.limits[]` of type Container, against each container of the namespace: the default that was injected, and the max that refuses | declared, not built |
| `QUOTA_BLOCKS` | ResourceQuota → pod creation | `status.used` against `status.hard` for each resource; `spec.scopes` and `scopeSelector` | declared, not built |
| `SCALES` | HPA → workload | `spec.scaleTargetRef`; `spec.behavior` (policies, stabilization window) | declared, not built |
| `SERVES_API` | APIService → Service | `spec.service` and `status.conditions[Available]`, for `custom.metrics.k8s.io` and `external.metrics.k8s.io` used by an HPA's metrics | absent |
| `ADMITS` | Mutating or ValidatingWebhookConfiguration → pod creation | `webhooks[].rules` (operations, resources), `namespaceSelector`, `objectSelector`, `failurePolicy`; `clientConfig.service` → Service | absent (webhooks are collected) |
| `CALLS` | Pod → Service | a host named in the pod's env or config (`<svc>`, `<svc>.<ns>`, `<svc>.<ns>.svc…`) that resolves to a Service of the graph | absent; weaker, confidence 0.8 |

A relation that holds is an edge with its method (`selector`, `ownerReferences`, `envFrom`…) and
`relations_version`. A relation whose selector matches nothing is recorded too, with no target: a Service with no
endpoint and a NetworkPolicy selecting nothing are evidence in themselves.

## 2. Vocabulary (G1)

Each new signal kind is defined from the documented message or condition of the Kubernetes component that writes
it, cited with the kind, never from a case's text:

| Kind | Read from |
|---|---|
| `admission.webhook_failed` | `FailedCreate`, `failed calling webhook "<name>"` (apiserver), with the webhook's name and the error: `x509`, connection refused, timeout |
| `admission.limit_refused` | `FailedCreate`, `forbidden: maximum <resource> usage per Container is <max>, but limit is <limit>` (LimitRanger), and the `minimum` form |
| `metrics.api_unavailable` | an APIService condition `Available=False`; HPA events `FailedGetExternalMetric`, `FailedGetPodsMetric`, `FailedComputeMetricsReplicas` |
| `hpa.scale_limited` | HPA condition `ScalingLimited=True` or `AbleToScale` with reason `ScaleUpStabilized`/`ScaleUpLimit`, with current and desired replicas |
| `config.parse_failed` | a container log naming a configuration file with a parse error (YAML/JSON patterns, fixed list) |
| `cache.write_refused` | Redis `OOM command not allowed when used memory > 'maxmemory'` |

The `unclassified` counter stays. A kind is added only when a source documents the message.

## 3. Time (G3)

- A fault signal is **ongoing** when the condition still holds at the time of collection. For example, an
  OOMKill is ongoing only if the container is not ready now, or has restarted since the symptom began.
- A signal that is over before the symptom began becomes context, the existing `restart.past` and `event.past`,
  and never a cause. This extends the rule h022 already applies to lifecycle warnings: an OOMKill seen through
  `lastState.terminated` on a ready container is `restart.past`, and no longer `container.oom_killed` as well.
- **Every hop must respect time**: a hop's cause starts no later than its effect. When both times are known and
  the order is reversed, the hop is dropped and recorded as such. When a time is unknown, the hop is kept, with
  its confidence × 0.8.
- The symptom's onset is the earliest event_time among the fault signals of its object.

## 4. The engine

1. **Symptoms.** The ongoing fault signals, grouped by object: a pod not ready, a creation refused, an SLO burn,
   an error rate.
2. **The walk.** From each symptom, follow the relations of § 1 **backward**, toward what acts on the object.
   At most 4 hops. Each object is visited once per chain.
3. **The abnormality check.** At each object reached, evaluate a fixed predicate for its kind:
   - a quota is at its hard limit;
   - a Service `targetPort` matches no `containerPort` of the pods it selects;
   - a webhook's Service has no ready endpoint, or its certificate has expired;
   - an APIService is unavailable;
   - a NetworkPolicy selects the pod and no rule admits the peer it needs;
   - a LimitRange's default is below the container's peak, or its max is below its limit;
   - an HPA is limited by its behaviour while the SLO burns;
   - a ConfigMap was changed after the pods started, or fails to parse.

   The predicates are listed per kind in code, each with a test. None is per case.
4. **The cause and its origin.** The cause is the farthest abnormal object of the chain. Its origin is the change
   that made it abnormal, when the graph has one: a drift, an edited label, a new version. The origin must come
   before the symptom, by § 3.
5. **The score**, from the existing rules (`docs/structured-diagnosis.md` § Score):
   - 0.5 for a chain whose last hop is abnormal;
   - + 0.15 for each hop whose abnormality is a signal from an independent source;
   - × the product of the hop confidences;
   - − 0.3 for each contradiction, for example a hop's target that is healthy now.

   Chains are ranked by score, then by length (the shortest first), then by fqn.
6. **Abstention** is unchanged: no chain reaching 0.5 gives `insufficient_evidence` when fault signals exist,
   `no_incident` otherwise. The existing rules still run. When a chain explains their symptom further, it
   replaces them, and the shorter answer is kept as the chain's first hop.

The answer names the cause and its mechanism (from the predicate's template), then lists the hops: `Pod … ←
EXPOSES (targetPort 8080, containerPort 9090) ← Service …`.

## 5. Measurement, fixed before any code

- **S2 is now a development set.** Its 21 cases and D's answers on them are known. A result on S2 measures the
  implementation, not generalization, and is reported as such.
- **Validation: a new set, S2b**, written and frozen before the engine's code (`docs/s2b-validation-cases.md`):
  - 33 cases in 11 families: S2's 7 with new chains, and 4 that S2 does not have;
  - a third of the cases with no incident;
  - the scenarios written by an author other than the engine's.
- **The control.** D as it is today is run on S2b before the engine exists, with the same grading
  (`docs/s2/grading.md`, unchanged).
- **The engine is kept** only if, on S2b, all of the following hold:
  - it finds **at least 4 more causes than the control**, and at least a third of S2b's causes. With a
    control that may find none, "more than the control" would be met by a single case. This is a decision rule
    fixed now, not a significance test: S2b is too small for one;
  - it recognises at least as many cases with no incident as the control;
  - D's S1 results stay 19 of 19, with the S1 negative cases still answered as no incident.

  Otherwise it stays off by default, and the result is reported anyway.
- **Each relation and predicate** is measured on the cases that use it, as `docs/structured-diagnosis.md` § 4
  does for each family. A relation that helps no case, or breaks a negative case, is not kept.
- **A second question, measured on its own:** given the chains as context, does a model (L₀) still invent a
  fault on the cases with no incident? Its arm is added to protocol 002 by a dated amendment before it is run.

## 6. Order

1. Review this spec.
2. S2b: the spec of its cases, the cases, the freeze. Then the control: D today on S2b, offline.
3. The relations of § 1 in the graph, each tested on fixtures that are neither S2 nor S2b.
4. The vocabulary (§ 2), then the time rules (§ 3).
5. The engine (§ 4) and its report.
6. The measures of § 5, offline. Then a live block for the models, on its own go.

## Not in scope

- New collectors. Every object above is already collected, typed or as a generic entity by API discovery
  (`ingestion/k8s_collector.py`). The relations read their fields from what is collected.
- Learned correlation: training, embeddings, or a model choosing the hops.
- Remediation, and the human gate.
