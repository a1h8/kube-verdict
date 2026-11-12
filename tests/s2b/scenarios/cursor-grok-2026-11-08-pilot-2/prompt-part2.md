# Brief v2 — Kubernetes incident scenarios where the cause is several steps from the symptom

You are writing test scenarios for a tool that investigates Kubernetes incidents. Each scenario describes a
cluster at one moment, a question an operator asks, and the true answer. The scenarios will be turned into
fixtures (YAML objects, events, alerts, logs) and used to measure tools that answer the question. You are not told
how those tools work, and you should not guess: write what a careful human analyst would need, and what would
mislead a hasty one.

## What every scenario must have

1. **A question** an operator would ask, about the object where the symptom shows. For example: "why can't
   checkout roll out in shop", "why is search slow".
2. **A cause at least two hops from the symptom.** A hop is a relation Kubernetes defines between two objects,
   carried by fields of those objects: a selector, an ownerReference, a volume or envFrom, a port, a quota, a
   LimitRange, an HPA's target, an admission webhook's rules, a NetworkPolicy's peers, a PVC's StorageClass, a
   ServiceAccount's imagePullSecrets, and so on. The object at fault is not the object the question names.
3. **The chain, hop by hop.** For each hop, give:
   - the two objects (`Kind/namespace/name`);
   - the relation;
   - the exact fields, on both objects, that carry it;
   - the event, log, alert or status that shows the target is abnormal.

   Each hop must be checkable from the objects alone, without the question.
4. **An origin when there is one:** the change that made the object at fault abnormal (an edited field, a new
   chart version, a relabelled namespace, a rotated Secret), with its time.
5. **At least 2 decoys**, each with why it is not the cause. In every scenario with a cause, at least one decoy is
   of one of these kinds:
   - a fault that happened and was resolved before the symptom began (the object is healthy now);
   - a difference between declared and live configuration that has no effect on the symptom;
   - the object nearest to the symptom, the one a hasty answer would name.
6. **Size:** tens of objects across at least 2 namespaces, most of them healthy and unrelated, as in a real
   cluster.
7. **Time:** give every event, change and alert a time relative to T0. The order must hold: a cause starts before
   its effect.
8. **A source:** at least one public document (Kubernetes documentation, a component's documentation, a published
   runbook) that describes the mechanism.

**Scenarios with no incident.** A third of the scenarios have no incident: the operator asks, and nothing is
wrong. They hold things that look wrong but are not:
- faults already over;
- warnings in another namespace with no effect;
- drifts that change nothing;
- an HPA at a high but healthy replica count.

Their true answer is "No incident".

## Families: 3 scenarios each, 2 with a cause and 1 with no incident

| Family | What its causes are about |
|---|---|
| `quota` | a ResourceQuota filled, by something other than the workload that is refused |
| `netpol` | a NetworkPolicy that blocks traffic the workload needs |
| `dependency` | a dependency (Service, database, cache, queue) that a workload calls and that fails or is unreachable |
| `limitrange` | a LimitRange whose defaults or bounds affect a workload |
| `hpa` | a HorizontalPodAutoscaler that does not scale as the load needs |
| `webhook` | an admission webhook that rejects or changes pods |
| `configmap` | a ConfigMap whose content or change breaks a workload |
| `pdb` | a PodDisruptionBudget |
| `ingress` | an Ingress and what it routes to |
| `storage` | PersistentVolumeClaims, PersistentVolumes, StorageClasses |
| `pullsecret` | image pull credentials |

Within a family, make the two causes differ in the object at fault and the path to it, not only in names or
numbers. Do not use a chain that you would consider the textbook example of the family: prefer the second or third
way the family fails in production.

## What the tool can see

The tool runs with a list of namespaces. It reads:

- **Kubernetes objects, typed:** Namespaces, Nodes, Pods, Deployments, ReplicaSets, StatefulSets, DaemonSets,
  Services, Ingresses, ConfigMaps, PersistentVolumes, PersistentVolumeClaims, ServiceAccounts, NetworkPolicies and
  MutatingWebhookConfigurations. Also events, with their reason, message, involved object and times.
- **Any other namespaced kind**, through API discovery, as a generic object with its spec and status: Jobs,
  CronJobs, ResourceQuotas, LimitRanges, HorizontalPodAutoscalers, PodDisruptionBudgets, cert-manager Certificates
  and Issuers, ArgoCD Applications and other custom resources.
- **Not read:**
  - **Secrets**: the tool never collects them, neither values nor metadata.
  - **Cluster-scoped kinds other than Namespaces, Nodes, PersistentVolumes and MutatingWebhookConfigurations**:
    APIServices, StorageClasses, ClusterIssuers, ValidatingWebhookConfigurations and others are not seen. A hop may
    still name such an object, but its evidence must come from what is read, for example an event, a condition on a
    namespaced object, or a log line.
  - **Endpoints**: a Service's readiness shows through its pods' readiness and the events.
- **Helm:** the release itself (`helm list`, `helm get manifest`, its values, its history). A "Helm release" in a
  scenario is always this release, never the Flux `HelmRelease` custom resource.
- **Prometheus:** alerts that fire or have fired, with their labels.
- **Loki:** log lines of containers.
- **OpenTelemetry:** traces, with error spans.

Everything a hop needs must be in one of these sources. Write messages as the real components write them,
following their documentation.

## Rules added in v2

1. **Partial credit never contains the answer.** `partial_if_only` lists what is on the path but stops short: the
   first hop, the symptom, or the right object with a wrong mechanism. It never contains `cause_object`, nor anything
   `passes_if` accepts.
2. **Scenarios with no incident.**
   - The question asks about the present ("is something wrong with X in Y", "why is X slow in Y"), and at T0 nothing
     is wrong.
   - `mechanism` says why each thing that looks wrong is not a fault. It names no cause, inside or outside the
     cluster.
   - `passes_if` is exactly "states that there is no incident and names no fault".
   - `chain` is empty, and the question is never about intended behaviour ("why is X allowed").
3. **Every hop is a relation Kubernetes defines, carried by fields of both objects.** A NetworkPolicy selects pods
   (`podSelector`, `namespaceSelector`), not Services. "Object → its own Namespace" is not a hop, and
   "Deployment → its ReplicaSet" does not count as one of the two. A scenario has at least two real hops between the
   symptom and the cause object.
4. **Every abnormal state is visible in the sources above.** A fact outside Kubernetes, such as a database schema
   change, is visible only through a log line, an alert or a trace that you quote, with its time.
5. **Events come from the component that emits them, worded as it words them.** Pod creation failures
   (`FailedCreate`) are events on the ReplicaSet or Job, not on the Deployment. Image pull failures are events on the
   Pod, from the kubelet.
6. **Quotas and limits count what the documentation says they count.**
   - Quotas count `requests` and `limits`, not usage.
   - A `pods` quota counts pods in a non-terminal state only.
   - LimitRange is enforced by the built-in LimitRanger admission plugin, not by a webhook.
7. **One cause, one origin, no ambiguity.** If two changes could each explain the symptom, the timeline shows which
   one did.
8. **Decoys are visible at T0** in the objects, events, alerts or logs. Give each decoy the field or event that shows
   it. "Removed yesterday" is not visible.
9. **Size.** `objects` lists at least 25 other objects across at least 2 namespaces, each with kind, namespace, name
   and a one-line state. They are the cluster around the incident, varied as a real one is, not the same list in
   every scenario.
10. **Timeline consistency.** Every time in `chain`, `decoys` and `mechanism` appears in `timeline`, and they agree.
11. **No textbook chains.** Prefer the second or third way a family fails in production. Two scenarios of a family
    differ in the cause object's kind or in the path, not only in names.
12. **Admission webhooks are MutatingWebhookConfigurations.** The tool reads no ValidatingWebhookConfiguration.

## Output: one YAML document per scenario

```yaml
id: <family>-<n>              # e.g. storage-1; storage-0 for the one with no incident
family: <family>
question: "..."
expected_outcome: cause       # or no_incident
cause_object: Kind/namespace/name      # omitted when no_incident
origin_object: Kind/namespace/name     # optional
mechanism: "one or two sentences: how the cause produces the symptom"
passes_if: "what an answer must name to be right"
partial_if_only: ["what earns only partial credit, e.g. the first hop alone"]
chain:
  - from: Kind/namespace/name
    relation: "..."
    to: Kind/namespace/name
    fields: "the fields on both objects that carry the relation"
    abnormal: "the event, log, alert or status that shows it, with its time"
decoys:
  - object: Kind/namespace/name
    looks_like: "..."
    why_not: "..."
timeline:
  - "T0-5h: ..."
  - "T0: ..."
objects: "a list of the other objects to include, healthy, with their namespaces"
sources: ["https://kubernetes.io/docs/..."]
```

Separate the YAML documents with `---`. Write the scenarios the request asks for, in the families it names. If you
stop before the end, say where. Write nothing about how a tool should find the cause.

---

Write the following scenarios, following the brief exactly, one YAML document each, separated by ---:
- one scenario with a cause in family `hpa` (id `hpa-1`)
- one scenario with a cause in family `webhook` (id `webhook-1`)
- one scenario with a cause in family `configmap` (id `configmap-1`)
- one scenario with a cause in family `pdb` (id `pdb-1`)
Output only the YAML documents.
