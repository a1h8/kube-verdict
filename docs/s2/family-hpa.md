# S2, family HPA — the autoscaler does not scale, and the reason is not in the HPA's own namespace

> Family spec, written 2026-11-07, before any fixture or `expect.json` of this family, under
> `docs/s2-correlation-cases.md`. Status: draft, to approve before the fixtures.

## The family

A workload saturates under load and its HorizontalPodAutoscaler does not add replicas, or adds them too slowly.
The HPA's conditions say what it cannot do; why is elsewhere: in the metrics pipeline it depends on, owned by
another team in another namespace, or in a scaling setting changed by a release. An answer that says "the HPA
cannot scale" names the symptom's gate.

**What the product already covers:** S1's h007 is metrics-server not returning pod metrics, and the product has a
rule for an HPA that reports it cannot read resource metrics (`hpa.metrics`, `rca/diagnosis.py`). This family uses a
custom metric served through an aggregated API, and a scaling policy: neither is covered.

**Sources** (outside the product):

- prometheus-operator/runbooks @ a685d14, `KubeAggregatedAPIDown` and `KubeAggregatedAPIErrors` (impact: "unable to use
  custom metrics to scale"); `KubeHpaReplicasMismatch` ("HPA has not matched the desired number of replicas").
- Kubernetes documentation, Horizontal Pod Autoscaling
  (https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/): custom metrics come from the
  `custom.metrics.k8s.io` API, served by an adapter registered as an APIService; `behavior.scaleUp` policies and the
  stabilization window limit how fast it scales. Wording checked and quoted when the fixtures are written.

**Where the graph is blind today:** the HPA is not typed (the `SCALES` edge is not created), nor is an APIService.
Nothing links an HPA to the metrics API it reads, nor that API to the Service and namespace that serve it.

## Case a1 — the custom metrics API points to a namespace the adapter left

**Question:** "why doesn't api scale up under load in shop"

**Chain** († = no edge in the graph today):

1. `Deployment/shop/api` is saturated (p99 latency alert); `HorizontalPodAutoscaler/shop/api` † stays at 4 replicas.
2. → the HPA's condition `ScalingActive=False`, reason `FailedGetPodsMetric`: "unable to get metric
   http_requests_per_second: … the server is currently unable to handle the request".
3. → `APIService/v1beta1.custom.metrics.k8s.io` † is `Available=False`, reason `MissingEndpoints`; it points to the
   Service `monitoring/prometheus-adapter`.
4. → **cause:** at T0 the prometheus-adapter release was reinstalled into `observability` (an observability-stack
   migration). It runs there, healthy. Its old Service in `monitoring` has no endpoints, and the APIService, a
   cluster-scoped object outside the release, still points to it.

**Evidence for each hop** (what an analyst reads, and what joins it to the next):

| Hop | Evidence in the fixtures | Joined to the next by |
|---|---|---|
| api saturates, does not scale | `prometheus/alerts.json`: `HighLatencyP99` on `shop/api` from T0+55 min; `kube/hpa.yaml`: `currentReplicas: 4`, `desiredReplicas: 4`, `minReplicas: 4` | the HPA's `scaleTargetRef: Deployment/api` |
| the HPA cannot read its metric | `kube/hpa.yaml` condition `ScalingActive=False`, `FailedGetPodsMetric`, message naming `http_requests_per_second` and "the server is currently unable to handle the request"; `kube/events.yaml`: `FailedGetPodsMetric` events on the HPA every 30 s since T0+2 min | the metric is a `type: Pods` metric, served by `custom.metrics.k8s.io` |
| the custom metrics API is unavailable | `kube/apiservices.yaml`: `v1beta1.custom.metrics.k8s.io`, `Available=False`, reason `MissingEndpoints`, `spec.service: {namespace: monitoring, name: prometheus-adapter}` | the Service `monitoring/prometheus-adapter` |
| that Service has nothing behind it | `kube/endpoints.yaml`: `monitoring/prometheus-adapter` with no address; `kube/services.yaml`: the Service, selector `app.kubernetes.io/name: prometheus-adapter` | the selector: no pod with that label in `monitoring` |
| the adapter now runs elsewhere | `kube/pods.yaml`: `observability/prometheus-adapter-…` Ready, started at T0+1 min; `helm/releases.json`: release `prometheus-adapter` uninstalled from `monitoring` and installed in `observability` at T0 | the timestamps: T0 for the move, T0+2 min for the first `FailedGetPodsMetric` |

**Cause object:** `APIService/v1beta1.custom.metrics.k8s.io`. **Mechanism:** it still points to
`monitoring/prometheus-adapter`, which has had no endpoints since the adapter moved to `observability`, so the custom
metrics API is unavailable and the HPA cannot read the metric it scales on.

**Proposed grading:** passes if it names the APIService (or the custom metrics API) pointing to the adapter's old
location. "The HPA cannot read its metric", or "the metrics API is unavailable", without where it points, is PARTIAL.

**Decoys:**

- `Deployment/observability/prometheus-adapter` is healthy and Ready: the adapter works, at its new address.
- metrics-server in `kube-system` is healthy and CPU metrics are available: the resource metrics API works.
- api's own pods are Ready, with high CPU: the load, not a fault.

**Time order:** the adapter migration at T0; the load ramp from T0+40 min; the latency alert at T0+55 min.

## Case a2 — a release slowed the HPA's scale-up policy

**Question:** "why does api take so long to absorb traffic peaks in shop"

**Chain:**

1. `Deployment/shop/api` breaches its latency SLO at each traffic peak, then recovers.
2. → `HorizontalPodAutoscaler/shop/api` † wants 12 replicas and adds one every 10 minutes; its condition
   `ScalingLimited=True`, reason `ScaleUpLimit`: "the desired replica count is increasing faster than the maximum
   scale rate".
3. → its `behavior.scaleUp` is one pod per 600 s, with a 600 s stabilization window.
4. → **cause:** the api release at T0 (chart 3.2.0) set that `behavior` from the chart's new defaults, meant for a
   batch workload. Before T0 the HPA had no `behavior` and used the Kubernetes default, which doubles the replicas
   every 15 s.

**Evidence for each hop:**

| Hop | Evidence in the fixtures | Joined to the next by |
|---|---|---|
| api breaches its SLO at each peak | `prometheus/alerts.json`: `LatencySLOBreach` on `shop/api`, active since the last peak; its description gives the burn over the last three peaks | the peak times, the same in every hop below |
| the HPA adds one pod every 10 minutes | `kube/events.yaml`: `SuccessfulRescale` on `shop/api` "New size: 5", "6", "7"… ten minutes apart at each of the three peaks since T0; `kube/hpa.yaml`: `desiredReplicas: 12`, condition `ScalingLimited=True`, `ScaleUpLimit` | the HPA object's `behavior` |
| its policy is one pod per 600 s | `kube/hpa.yaml`: `behavior.scaleUp: {stabilizationWindowSeconds: 600, policies: [{type: Pods, value: 1, periodSeconds: 600}]}` | the HPA's Helm annotations: release `api` |
| before T0 it scaled fast | `kube/events.yaml`: the `SuccessfulRescale` events of the two peaks before T0, "New size: 8" then "12" within a minute | the change between the two series is at T0 |
| the release at T0 set that policy | `helm/history.json`: revision 12 at T0, chart `api-3.2.0` (revision 11, `api-3.1.4`, before); `helm/manifest.yaml` (applied, revision 12): the HPA with that `behavior`; `helm/manifest-previous.yaml` (revision 11): the HPA without `behavior` | — |

**Cause object:** `HorizontalPodAutoscaler/shop/api`, through the T0 release. **Mechanism:** its scale-up policy (one
pod per 600 s, 600 s stabilization), introduced by the T0 chart, limits how fast it adds replicas, so each peak
saturates api before enough replicas exist.

**Proposed grading:** passes if it names the HPA's scale-up policy or rate limit. "The HPA scales too slowly"
without the policy is PARTIAL; naming the release without the policy is PARTIAL.

**Decoys:**

- The traffic peaks themselves (daily, at the same hours as before T0): the trigger, not the cause.
- `maxReplicas` is 20, never reached: not the limit.
- The node pool has room (no Pending pod): not capacity.

**Time order:** the release at T0; the first slow peak at T0+5 h; two more peaks since.

## Case a0 — negative: the same objects, nothing wrong

**Question:** "is something wrong with api in shop"

The APIService points to the adapter in `observability`, and is Available; the HPA has the batch-free default
behavior and follows the load; no latency alert. The decoys stay: metrics-server healthy, api's CPU varying with
the load, a daily peak absorbed. **Expected outcome:** `no_incident`.

## Size of each case

About 45 objects over `shop`, `monitoring`, `observability` and `kube-system`, plus the cluster-scoped APIServices:
Deployments, pods, Services with endpoints, the HPA, events, Helm values and the applied manifest of the release in
question, Prometheus alerts.

## What this family can show, and what it cannot

- **What it can show:** whether an arm goes from an HPA's condition to the API or setting behind it. The product's
  HPA rule reads resource-metric failures, so on a1 it may say "the HPA cannot read metrics": PARTIAL, to measure.
- **What it cannot show:** KEDA, VPA, or cluster-autoscaler interactions.

## Reworked 2026-11-07, before any fixture

The first version listed each hop without saying where it shows. Every hop now has its evidence and its join,
as `docs/s2-correlation-cases.md` requires; three files were added for it: `kube/apiservices.yaml`,
`helm/releases.json` (a1) and `helm/manifest-previous.yaml` (a2).

## Decided 2026-11-07

1. a1: "the metrics API is unavailable" without where it points is PARTIAL.
2. a2: the release named without the scale-up policy is PARTIAL.

## Fixtures, 2026-11-07

`tests/s2/build_family_hpa.py` writes a1, a2 and a0 to `tests/s2/cases/` (46 to 69 objects, 4 namespaces); their hops
are checked against the fixtures from `tests/s2/evidence.yaml`. Seen when loading them, without running any arm: the
`KubeAggregatedAPIErrors` alert of a1 is kept with no entity to attach it to, since the graph has no APIService.
