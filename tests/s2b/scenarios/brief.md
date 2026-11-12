# Brief — Kubernetes incident scenarios where the cause is several steps from the symptom

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

- **Kubernetes objects:** Namespaces, Nodes, Pods, Deployments, ReplicaSets, StatefulSets, DaemonSets, Jobs,
  CronJobs, Services and their Endpoints, Ingresses, ConfigMaps, PVCs, PVs, StorageClasses, ServiceAccounts,
  NetworkPolicies, ResourceQuotas, LimitRanges, HPAs, PodDisruptionBudgets, admission webhook configurations,
  APIServices, cert-manager Certificates and Issuers. Also events, with their reason, message, involved object
  and times. The values of Secrets are never read.
- **Helm:** the release, its applied manifest, its values, its history.
- **Prometheus:** alerts that fire or have fired, with their labels.
- **Loki:** log lines of containers.
- **OpenTelemetry:** traces, with error spans.

Everything a hop needs must be in one of these sources. Write messages as the real components write them,
following their documentation.

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

Write the 33 scenarios. If a family is too large for one answer, write the families in order and say where you
stopped. Write nothing about how a tool should find the cause.
