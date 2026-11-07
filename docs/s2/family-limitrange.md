# S2, family LimitRange — the namespace sets a limit nobody declared

> Family spec, written 2026-11-06, before any fixture or `expect.json` of this family, under
> `docs/s2-correlation-cases.md`. Status: draft, to approve before the fixtures.

## The family

A container gets a limit, or is refused, by a rule that is not in its Deployment, its chart or its Git
history: a LimitRange of its namespace, applied by the admission controller when the pod is created. The
pod's spec shows the result; the workload's own manifest never mentions it. An answer that blames the
workload's configuration names the wrong owner.

**Sources** (outside the product):

- Kubernetes documentation, Limit Ranges (https://kubernetes.io/docs/concepts/policy/limit-range/): a
  LimitRange sets default requests and limits for containers that declare none, and minimum and maximum
  constraints; it is enforced at admission, so it changes new pods only, never running ones. The LimitRanger
  admission plugin records what it set in the pod's `kubernetes.io/limit-ranger` annotation. Wording checked
  and quoted when the fixtures are written.
- prometheus-operator/runbooks @ a685d14, `KubePodCrashLooping` ("app responding extremely slow due to
  resource constraints such as memory too low"); `KubeDeploymentReplicasMismatch`.

**Where the graph is blind today:** the LimitRange is not typed (the test loader skips it, and the product's
`LIMITS` edge is not created). Nothing links a pod's limit to the object that set it, and the applied manifest of
the release, which has no limit, does not differ from the live Deployment, which has none either: the injected limit
is only in the pod.

## Case l1 — a default memory limit injected into a container that declares none

**Question:** "why does report-worker keep crashing in shop"

**Chain** († = no edge in the graph today):

1. `Pod/shop/report-worker-…` restarts, OOMKilled, about every 20 minutes under load.
2. → the pod's container has `limits.memory: 256Mi`, and the pod carries
   `kubernetes.io/limit-ranger: "LimitRanger plugin set: memory limit for container report-worker"`.
3. → `Deployment/shop/report-worker` and its chart declare requests only, no limit.
4. → **cause:** `LimitRange/shop/container-defaults` † was created at T0 by the platform team, with a default
   memory limit of 256Mi; report-worker's pods were recreated by a node drain at T0+30 min and got the default.
   The worker's heap needs about 700Mi.

**Cause object:** `LimitRange/shop/container-defaults`. **Mechanism:** its default memory limit (256Mi) was set by
admission on report-worker's new pods, which declare no limit, and the worker needs more, so it is OOMKilled.

**Proposed grading:** passes if it names the LimitRange's default as the source of the 256Mi limit. "The memory
limit is too low" without the LimitRange is PARTIAL: it would send the operator to a chart that has no limit to raise.

**Decoys:**

- The node drain at T0+30 min (events on `worker-node-02`): what recreated the pods, not why they crash.
- `Deployment/shop/cart-api` declares its own limits and is not affected: a control inside the case.
- A Helm values diff on report-worker's log level (`debug` in Git, `info` deployed): real, harmless.

**Time order:** the LimitRange at T0; the drain at T0+30 min; the first OOMKilled at T0+52 min.

## Case l2 — a lowered maximum refuses the new pods of a rollout

**Question:** "why is the search-api rollout stuck in shop"

**Chain:**

1. `Deployment/shop/search-api` rollout to 4.1.0 stalls: 3 old pods Running, the new ReplicaSet at 0.
2. → `ReplicaSet/shop/search-api-…` † `FailedCreate`: "maximum memory usage per Container is 1Gi, but limit is 2Gi".
3. → search-api's chart sets a 2Gi memory limit, unchanged for months.
4. → **cause:** `LimitRange/shop/container-defaults` † had its `max.memory` lowered from 4Gi to 1Gi at T0. The running
   pods, admitted before, are untouched; every new pod is refused.

**Cause object:** `LimitRange/shop/container-defaults`. **Mechanism:** its maximum memory per container was lowered
to 1Gi, below search-api's 2Gi limit, so admission refuses the rollout's new pods while the old ones keep running.

**Proposed grading:** passes if it names the LimitRange's maximum against search-api's limit. "Admission refuses the
pods" or "the limit is too high" without the LimitRange is PARTIAL.

**Decoys:**

- The new version 4.1.0 is what the rollout ships: the trigger, not the cause; the old version would be refused too.
- `search-api` CPU throttling on the old pods: real, unrelated.
- The ResourceQuota of `shop` at 60 %: room left, so not what refuses.

**Time order:** the LimitRange change at T0; the rollout at T0+15 min.

## Case l0 — negative: the same objects, nothing wrong

**Question:** "is something wrong in shop"

The objects of l1 and l2 with a LimitRange whose default (1Gi) and maximum (4Gi) fit every workload: report-worker
running with the injected 1Gi, search-api rolled out. The decoys stay: an old drain, the harmless log-level diff, the
CPU throttling, the quota at 60 %. **Expected outcome:** `no_incident`.

## Size of each case

About 45 objects over `shop`, `monitoring` and `kube-system`, plus the three Nodes: Deployments, ReplicaSets,
pods with their admission annotations, the LimitRange, the ResourceQuota, events, Helm values and the applied manifest
of the release in question, Prometheus alerts.

## What this family can show, and what it cannot

- **What it can show:** whether an arm attributes a limit to the object that set it rather than to the workload. The
  structured diagnosis has an OOM rule (`oom.limit`), so on l1 it may well say "the limit is too low", which is PARTIAL:
  the expected result to test.
- **What it cannot show:** other admission controllers that set resources (policy engines, VPA).

## Decided 2026-11-07

1. l1: "the memory limit is too low" without the LimitRange is PARTIAL.
2. l2: "admission refuses the pods" without the LimitRange is PARTIAL.

## Fixtures, 2026-11-07

`tests/s2/build_family_limitrange.py` writes l1, l2 and l0 to `tests/s2/cases/`. A first version had about 30 objects
in one namespace, under the set's own requirement (tens of objects over at least two namespaces): healthy workloads in
`monitoring` and `kube-system`, and the Services, were added before any arm ran.
