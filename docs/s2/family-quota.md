# S2, family ResourceQuota — the quota is full, and something else filled it

> Family spec, written 2026-11-06, before any fixture or `expect.json` of this family, under
> `docs/s2-correlation-cases.md`. Status: draft, to approve before the fixtures.

## The family

A namespace ResourceQuota refuses a new pod. The quota is where the symptom shows, not the cause:
the cause is whatever consumed it, often another workload, and the step that made it consume more.
An answer that stops at "the quota is full" names the symptom's gate; under amendment A1 it fails
the primary measure, since it names neither the cause object nor its mechanism.

**Sources** (outside the product, read 2026-11-06):

- prometheus-operator/runbooks @ a685d14, `KubeQuotaFullyUsed`: the namespace reached its allowed
  limits, and new installations may not be possible.
- the same, `KubeHpaMaxedOut`: an HPA at max replicas; among the causes to check, "too low value for
  requests such as CPU".
- the same, `KubePodNotReady`: Pending means the pod cannot be created for that namespace and node.
- Kubernetes documentation: ResourceQuota (https://kubernetes.io/docs/concepts/policy/resource-quotas/),
  HorizontalPodAutoscaler (utilization computed against the containers' requests), CronJob
  (`concurrencyPolicy`). Their wording is checked when the fixtures are written, and quoted in the case.

**Where the graph is blind today:** a ResourceQuota is linked to its namespace only, and that edge
(`QUOTA_BLOCKS`) is not created by the product's collectors; nor are `SCALES` (HPA to its workload) and
`SPAWNED_BY` (Job to CronJob). No edge leads from the refused pod to what used the quota.

## Case q1 — another workload's request drift fills the quota

**Question:** "why can't billing-worker roll out in shop"

**Chain** (each hop a relation between two objects; † = no edge in the graph today):

1. billing-worker's new pod is refused: `FailedCreate`, "exceeded quota: shop-quota, requested:
   requests.memory=2Gi, used: requests.memory=31Gi, limited: requests.memory=32Gi".
2. → `ResourceQuota/shop/shop-quota` † is at its `requests.memory` hard limit.
3. → the quota is used by 20 replicas of `Deployment/shop/catalog-api` †.
4. → `HorizontalPodAutoscaler/shop/catalog-api` † scaled it from 4 to its max of 20 (`KubeHpaMaxedOut`).
5. → **cause:** catalog-api's live CPU request is 100m against 500m in the release's applied manifest (a
   `kubectl set resources` after the last upgrade), so the HPA reads five times the utilization for the
   same load. Each replica still requests 1Gi of memory: 20 of them fill the memory quota.

*Corrected 2026-11-06, before any fixture:* the first version had the CPU quota filling. It cannot: with a
lower CPU request the HPA runs five times more replicas, each requesting five times less CPU, so the CPU
requested stays the same. What grows with the replica count is what each replica requests unchanged: memory.

**Cause object:** `Deployment/shop/catalog-api`. **Mechanism:** its CPU request drifted below the applied
value, so its HPA scaled it to max and its replicas' memory requests filled the namespace quota.

**Decoys** (each real and visible, each with why it is not the cause):

- `Deployment/shop/cart-api` runs an image tag one patch above its declared one, and every pod is Ready:
  a drift with no effect.
- `billing-worker` itself had an OOMKilled restart 5 h ago, before the rollout: old, resolved.
- `CPUThrottlingHigh` fires on a pod of `monitoring/`: another namespace, no quota there.
- `LimitRange/shop/shop-limits` sets default requests for containers that declare none; every container
  here declares its own, so it changes nothing.

**Time order:** the catalog-api change at T0; the HPA scale-ups from T0+10 min to T0+25 min; the quota
full at T0+25 min; billing-worker's rollout at T0+40 min.

## Case q2 — Jobs that never finish fill the quota

**Question:** "why does search-api not scale up in shop"

**Chain:**

1. `HorizontalPodAutoscaler/shop/search-api` wants 6 replicas; 3 exist; the new pods are refused:
   "exceeded quota: shop-quota, requested: pods=1, used: pods=40, limited: pods=40".
2. → `ResourceQuota/shop/shop-quota` † is at its `pods` limit.
3. → 25 of those pods are Jobs of `CronJob/shop/report-export` †, all Running, the oldest several hours.
4. → the CronJob runs every 10 minutes with `concurrencyPolicy: Allow` and no `activeDeadlineSeconds`.
5. → **cause:** each Job waits on `Service/data/reports-db` †, which has had no ready endpoint since
   T0, so none finishes, and Allow starts a new one every 10 minutes.

**Cause object:** `CronJob/shop/report-export`, with `Service/data/reports-db` as the origin of the hang.
**Mechanism:** Jobs that never finish accumulate under `concurrencyPolicy: Allow` and fill the namespace's
pod quota. An answer passes if it names the CronJob and why its Jobs accumulate; naming the Service alone
is PARTIAL (decided 2026-11-06).

**Decoys:**

- `search-api`'s own pods show a CPU spike: the load the HPA reacts to, not a fault.
- `Deployment/data/reports-db-proxy` was rolled out at T0+2 h: after the hang began.
- `Deployment/shop/notify-worker` was rolled out at T0+2 h 30 min: healthy, unrelated to the quota.

**Time order:** reports-db loses its endpoints at T0; one Job every 10 minutes from then on; the pod quota
full at about T0+4 h 10 min; the search-api scale-up refused afterwards.

## Case q0 — negative: the same objects, nothing wrong

**Question:** "is something wrong in shop"

The objects of q1 and q2, healthy: catalog-api at its declared request, its HPA at 4 of 20; the quota at
15 of 32 Gi of `requests.memory` and 15 of 40 `pods`; report-export with `concurrencyPolicy: Forbid`, its last Jobs
complete. The decoys stay: cart-api's harmless tag drift, the old restart, `CPUThrottlingHigh` in
`monitoring/`. **Expected outcome:** `no_incident`.

## Size of each case

About 40 objects: `shop` (8 Deployments, their ReplicaSets and pods, 2 HPAs, the quota, a LimitRange,
Services, ConfigMaps, the CronJob and its Jobs), `data` (the database Service, its endpoints, the proxy),
`monitoring` (the alerting pod); events, Helm values and releases for the charts involved, Prometheus
alerts. Most of it healthy.

## What this family can show, and what it cannot

- **What it can show:** whether an arm goes past "the quota is full" to what consumed it. The structured
  diagnosis has a quota rule (`quota.exceeded`), so it may well stop at the quota, which is PARTIAL: that is
  the expected result to test, not a defect to fix before the run.
- **What it cannot show:** anything for quota incidents that do not look like these two. Two cases with a
  fault are the minimum of protocol 002, not a sample of this family.

## Decided 2026-11-06

1. q2: the CronJob is required; the Service alone is PARTIAL.
2. q1: the LimitRange is a decoy, present in all three cases.

## Fixtures, 2026-11-06

`tests/s2/build_family_quota.py` writes the three cases to `tests/s2/cases/` (64, 86 and 134 objects).
Fixed while writing them, before any arm ran: the CPU limit stays at its applied value when only the request
changes (`kubectl set resources --requests`); the HPA-managed Deployments leave `spec.replicas` out of the applied
manifest, as charts with an HPA do, which removed a misleading "critical" replica drift; the ConfigMap decoy of q2
is replaced by a rollout of notify-worker (below).

**Two limits of the test loader, found here and not fixed** (`tests/integration/cases/case_loader.py`, frozen with S1):
it counts ConfigMaps as covered but builds none, so an applied ConfigMap is reported "absent from cluster"; and it
does not carry a Deployment's image where the applied-vs-live comparison looks for it, so cart-api's tag drift is in
the raw files but not in the graph. The first is why no ConfigMap is in the applied manifests here.
