# S2, family NetworkPolicy — the traffic stops because a label stopped matching

> Family spec, written 2026-11-06, before any fixture or `expect.json` of this family, under
> `docs/s2-correlation-cases.md`. Status: draft, to approve before the fixtures.

## The family

A pod cannot reach another one, or is not reached. Nothing is down: an allow rule that used to match no
longer does, because a label changed on a namespace or on a pod, and a default-deny policy now blocks the
traffic. The symptom shows on one workload; the policy is in another namespace; the change is a label.

**What the product already covers, and what it does not:** S1's h006 is a policy that allows no egress at
all (`egress: []`), and the product has one rule for it (`netpol.egress_blocked`, `rca/diagnosis.py`). This
family is the other kind, more common in practice: a policy that allows some traffic, through a selector that
no longer selects. No rule of ours covers it, and none will be written before S2 has run.

**Sources** (outside the product):

- Kubernetes documentation, Network Policies
  (https://kubernetes.io/docs/concepts/services-networking/network-policies/): policies are additive, a pod
  selected by any policy for a direction is isolated for it, `namespaceSelector` and `podSelector` match labels,
  and a default-deny policy selects every pod. The wording is checked and quoted when the fixtures are written.
- prometheus-operator/runbooks @ a685d14, `KubePodNotReady` (Running but not ready means the readiness probe
  fails; "app waits for other services to start, such as database") and `KubePodCrashLooping`.

**Where the graph is blind today:** a NetworkPolicy is linked to its namespace only (`RESTRICTS`), and that
edge is not created by the product's collectors. No edge links a policy to the pods its selector matches, to
the namespaces its `namespaceSelector` matches, or to the label that changed.

## Case n1 — a namespace relabel stops matching an ingress allow rule

**Question:** "why are checkout-api's payments failing in shop"

**Chain** († = no edge in the graph today):

1. `Pod/shop/checkout-api-…` logs time out calling `payments.pay.svc.cluster.local:8443`; its readiness, which
   checks that dependency, fails; the Deployment is 0/3 available.
2. → `Service/pay/payments` † has ready endpoints: payments itself is healthy.
3. → `NetworkPolicy/pay/default-deny-ingress` † isolates every pod of `pay`;
   `NetworkPolicy/pay/allow-from-storefront` † allows ingress from namespaces labelled `team: storefront`.
4. → **cause:** `Namespace/shop` † was relabelled at T0 from `team: storefront` to `team: shop` (an ownership
   cleanup), so the allow rule no longer selects it.

**Cause object:** `Namespace/shop`. **Mechanism:** its `team` label changed, so `allow-from-storefront` in `pay`
no longer matches it and the default deny drops checkout-api's calls to payments.

**Proposed grading:** an answer passes if it names the label mismatch between `Namespace/shop` and the allow
rule (naming the policy and the changed label is enough). Naming a NetworkPolicy without the mismatch is PARTIAL.

**Decoys:**

- `Deployment/pay/payments` rolled out at T0+5 min: healthy, every pod Ready, its endpoints ready.
- `Pod/kube-system/coredns-…` restarted 26 h ago: resolved, and DNS resolves in the logs.
- `NetworkPolicy/shop/allow-egress-all` in `shop`: allows every egress, so it is not what blocks.

**Time order:** the namespace relabel at T0; the first timeouts at T0+1 min; payments' rollout at T0+5 min.

## Case n2 — a chart upgrade changes pod labels that a policy selects

**Question:** "why does orders-api keep restarting in shop"

**Chain:**

1. `Pod/shop/orders-api-…` CrashLoopBackOff: "dial tcp 10.42.7.21:5432: i/o timeout" to `orders-db.data`.
2. → `Service/data/orders-db` † is healthy, with ready endpoints.
3. → `NetworkPolicy/data/default-deny-ingress` † isolates `data`; `NetworkPolicy/data/allow-orders-to-db` †
   allows ingress from pods labelled `app: orders-api` in namespaces labelled `team: storefront`.
4. → **cause:** the orders-api chart upgrade at T0 (2.x to 3.0) moved to the standard labels:
   its pods now carry `app.kubernetes.io/name: orders-api` and no longer `app: orders-api`.

**Cause object:** `Deployment/shop/orders-api`, through its chart upgrade. **Mechanism:** the upgrade changed the pod
labels, so `allow-orders-to-db` in `data` no longer selects orders-api's pods and the default deny drops their
connections to the database.

**Proposed grading:** passes if it names the pod-label change against the policy's `podSelector`. Naming the
upgrade without the label is PARTIAL, as is naming the policy without the label.

**Decoys:**

- `orders-db` shows a high-connections alert from a batch job at T0−3 h: resolved before T0.
- The chart upgrade also moved orders-api to a new image tag: expected with any upgrade, not a drift.
- `NetworkPolicy/shop/allow-egress-all`, as in n1.

**Time order:** the upgrade at T0; the first CrashLoopBackOff at T0+2 min.

## Case n0 — negative: the same objects, nothing wrong

**Question:** "is something wrong in shop"

The objects of n1 and n2 with labels that match: `Namespace/shop` labelled `team: storefront`, orders-api's pods
carrying both the old and the new labels (a chart that kept the old one for compatibility). Every workload Ready.
The decoys stay: payments' recent rollout, the old CoreDNS restart, the resolved database alert, the
`allow-egress-all` policy. **Expected outcome:** `no_incident`.

## Size of each case

About 50 objects (49 to 52) over `shop`, `pay`, `data` and `kube-system`: the Namespaces with their labels, Deployments,
pods, Services with endpoints, 5 NetworkPolicies, events, Helm values and the applied manifest of each release
involved, Loki logs, Prometheus alerts. No ConfigMap in an applied manifest (the loader limit recorded in
`family-quota.md`).

## What this family can show, and what it cannot

- **What it can show:** whether an arm joins a symptom in one namespace to a policy in another, through a
  label nobody wrote down as a link. We expect the structured diagnosis to have nothing to say here: its rule is
  for a policy allowing no egress. That is the point of the measure, not something to fix before it.
- **What it cannot show:** CNI-level faults, egress gateways, or policies of other engines (Cilium, Calico).

## Decided 2026-11-06

1. n1: the label mismatch passes, whether the answer names the Namespace or the policy; a NetworkPolicy named
   without the mismatch is PARTIAL.
2. n2: the policy and the pod label pass; the upgrade named without the label is PARTIAL.
3. `allow-orders-to-db` selects the namespace by its immutable `kubernetes.io/metadata.name` label, as policies
   usually do, so n1's relabel breaks only checkout-api's calls, not orders-api's.
4. n2's second decoy is the image tag the chart upgrade brought, expected with any upgrade, instead of a memory
   limit change that the evidence would not show without the previous manifest.

## Fixtures, 2026-11-06

`tests/s2/build_family_netpol.py` writes n1, n2 and n0 to `tests/s2/cases/`. As expected, the product's loader
builds no edge from a NetworkPolicy to anything, and no rule annotation fires on these cases (checked by loading
them, without running any arm).
