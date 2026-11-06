# S2, family dependency in another namespace — the client fails, the fault is behind it

> Family spec, written 2026-11-06, before any fixture or `expect.json` of this family, under
> `docs/s2-correlation-cases.md`. Status: draft, to approve before the fixtures.

## The family

A workload fails, and nothing about it is wrong: what it calls, in another namespace, is broken or
unreachable. The symptom shows on the client; the cause sits one or two services away, owned by another
team and often another release. An answer that stops at the client's errors names the symptom.

**Sources** (outside the product):

- prometheus-operator/runbooks @ a685d14, `KubePodNotReady`: among the causes, "app waits for other services
  to start, such as database"; `KubeDeploymentReplicasMismatch`.
- Kubernetes documentation, Services (https://kubernetes.io/docs/concepts/services-networking/service/): a
  Service's `targetPort` is the port the traffic reaches on the pods its selector matches, and its endpoints list
  the ready pods it selects. DNS for Services (`<service>.<namespace>.svc.cluster.local`).
- Redis documentation on `maxmemory` and `maxmemory-policy` (https://redis.io/docs/latest/develop/reference/eviction/):
  with `noeviction`, writes are refused once the limit is reached ("OOM command not allowed when used memory >
  'maxmemory'"). Wording checked and quoted when the fixtures are written.

**Where the graph is blind today:** no edge links a Service to the pods it selects through a port, nor one
service to another. Between services, the only paths are indirect: logs and error traces attached to a pod
(`HAS_LOG`, `HAS_TRACE`), which name the callee as text.

## Case d1 — the callee is healthy, its Service targets the wrong port

**Question:** "why does checkout-api fail to reserve stock in shop"

**Chain** († = no edge in the graph today):

1. `Pod/shop/checkout-api-…` logs "connection refused" to `inventory.platform.svc.cluster.local:80`; checkout's
   reservations fail, its error-rate alert fires.
2. → `Service/platform/inventory` † has ready endpoints: the inventory pods are Ready.
3. → the Service sends traffic to `targetPort: 8080` †.
4. → **cause:** the inventory chart upgrade at T0 (release `inventory`) moved the container to port 9090 and its
   readiness probe with it, while the Service belongs to the `platform-services` release and still targets 8080.
   The pods are Ready on 9090 and nothing listens on 8080.

**Cause object:** `Service/platform/inventory`. **Mechanism:** its `targetPort` (8080) no longer matches the port
the inventory container listens on since the upgrade (9090), so every connection to the Service is refused while
the pods report Ready.

**Proposed grading:** passes if it names the port mismatch between the Service and the inventory pods, whichever
of the two it calls the culprit. "inventory is unreachable" or "connection refused" without the port is PARTIAL.

**Decoys:**

- `checkout-api` shows CPU throttling (`CPUThrottlingHigh`): real, unrelated, older than T0.
- `Deployment/platform/inventory` has new image tag 2.0.0 with the upgrade: expected.
- A warning event on `Pod/platform/inventory-…`: one readiness failure during the rollout, then Ready.

**Time order:** the inventory upgrade at T0; the first refused connections at T0+1 min.

## Case d2 — the callee fails because of its own dependency

**Question:** "why does checkout-api return errors on pricing in shop"

**Chain:**

1. `Pod/shop/checkout-api-…` logs "pricing returned 503"; error traces from checkout-api show the failing span
   is the call to `pricing.platform`.
2. → `Pod/platform/pricing-…` logs and traces: its writes to `redis.cache` fail with "OOM command not allowed
   when used memory > 'maxmemory'".
3. → `Deployment/cache/redis` † runs with `--maxmemory 512mb --maxmemory-policy noeviction`.
4. → **cause:** a pricing release at T0 started caching every price request without a TTL; Redis filled to its
   `maxmemory` at about T0+3 h and, under `noeviction`, refuses every write since.

**Cause object:** `Deployment/cache/redis`, with pricing's caching as the origin. **Mechanism:** Redis reached its
`maxmemory` with the `noeviction` policy, so it refuses writes; pricing fails on them and returns 503 to checkout.

**Proposed grading:** passes if it names Redis's memory limit (or its eviction policy) as what refuses the writes.
Naming pricing as failing, without Redis's limit, is PARTIAL. Naming pricing's TTL-less caching together with the
limit passes too.

**Decoys:**

- A `Certificate` in `shop` expires in 12 days: a warning, not yet a fault.
- `Deployment/platform/pricing` was scaled from 3 to 5 replicas at T0+3 h 10 min, by hand, as a reaction.
- checkout-api's p95 latency alert, which is the symptom, not a cause.

**Time order:** the pricing release at T0; Redis's used memory rising until T0+3 h; the first refused writes and
503s from then on; pricing's manual scale-up at T0+3 h 10 min.

## Case d0 — negative: the same objects, nothing wrong

**Question:** "is something wrong with checkout-api in shop"

The objects of d1 and d2: the inventory Service targeting the port the pods listen on, Redis at 40 % of its
`maxmemory`, every workload Ready. The decoys stay: checkout's CPU throttling, the certificate expiring in 12 days,
the old readiness blip during a rollout. **Expected outcome:** `no_incident`.

## Size of each case

About 50 objects over `shop`, `platform`, `cache` and `monitoring`: Deployments, pods, Services with endpoints,
events, Helm values and the applied manifest of the release in question, Loki logs, OTel error traces (d2),
Prometheus alerts. No ConfigMap in an applied manifest (the loader limit recorded in `family-quota.md`); Redis's
settings are in its container arguments.

## What this family can show, and what it cannot

- **What it can show:** whether an arm follows the client's errors to the callee, then to the callee's own
  configuration or dependency. The traces of d2 are the one path the graph has; d1 has none, only text.
- **What it cannot show:** a service mesh (retries, circuit breakers), or a dependency outside the cluster.

## Decided 2026-11-06

1. d1: the port mismatch passes whichever side the answer blames, the Service or the upgrade; "unreachable" or
   "connection refused" without the port is PARTIAL.
2. d2: Redis's memory limit (or its eviction policy) is required; pricing named as failing without it is PARTIAL;
   pricing's TTL-less caching together with the limit passes.

## Fixtures, 2026-11-06

`tests/s2/build_family_dependency.py` writes d1, d2 and d0 to `tests/s2/cases/` (48 to 52 objects). Seen when
loading them, without running any arm: **the product collects none of d2's error traces.** Its OTel collector asks
only for the traces of degraded workloads, and checkout-api and pricing are Ready while they fail requests. So the
one path between services that this spec counted on is not taken; the traces stay in the raw files. Recorded as
found, not fixed: S2 measures the product as it is.
