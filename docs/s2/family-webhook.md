# S2, family mutating webhook — what runs is not what any chart declares

> Family spec, written 2026-11-07, before any fixture or `expect.json` of this family, under
> `docs/s2-correlation-cases.md`. Status: draft, to approve before the fixtures.

## The family

A mutating admission webhook changes pods as they are created: it injects a sidecar, sets a field, or, when
it is down and set to fail closed, refuses them. The fault shows on workloads of several releases at once, in
the namespaces the webhook selects; the cause is the webhook, its configuration or its own health, owned by a
platform team in another namespace. No chart of the affected workloads mentions it.

**What the product already has:** the live policy collector adds a `MutatingWebhook` node per
`MutatingWebhookConfiguration` (`ingestion/policy_collector.py`) and the count lowers confidence
(`rca/confidence.py`). There is no edge from a webhook to the namespaces or pods it selects, nor to the Service it
calls, and no rule names a webhook as a cause. The test loader does not read webhooks at all: one more item for the
loader parity required before any S2 run.

**Sources** (outside the product):

- Kubernetes documentation, Dynamic Admission Control
  (https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/): a mutating webhook
  selects objects by `namespaceSelector` and `objectSelector`, is called through a Service, and with
  `failurePolicy: Fail` an unreachable webhook rejects the request. Wording checked and quoted when the fixtures are written.
- prometheus-operator/runbooks @ a685d14, `KubeContainerWaiting` (a container Waiting too long; check events and
  missing files), `KubeDeploymentReplicasMismatch`.

## Case w1 — an injector upgrade injects a sidecar image that does not exist

**Question:** "why are new pods of cart-api and web stuck in shop"

**Chain:**

1. New pods of `cart-api` and `web` in `shop` (two releases) are `ImagePullBackOff` on a container `mesh-proxy`;
   their older pods run.
2. → `mesh-proxy` is in the pods but in neither chart's manifest; the pods carry `mesh.acme.io/injected: "true"`.
3. → `MutatingWebhookConfiguration/mesh-injector` selects namespaces labelled `mesh-injection: enabled`, which
   `shop` is, and calls `mesh-system/mesh-injector`.
4. → **cause:** `Deployment/mesh-system/mesh-injector` was upgraded at T0 (chart 1.21.0), whose default proxy image is
   `ghcr.io/acme/mesh-proxy:1.21.0-rc`, a tag that was never published. Every pod created since T0 gets it.

**Cause object:** `Deployment/mesh-system/mesh-injector`. **Mechanism:** since its T0 upgrade it injects the proxy
image `1.21.0-rc`, which does not exist, into every new pod of the namespaces it selects, so they cannot start.

**Grading:** passes if it names the injector (or its webhook) as the source of the `mesh-proxy` container
with the bad image. "An image cannot be pulled" without where the container comes from is PARTIAL.

**Decoys:**

- `cart-api` was itself redeployed at T0+20 min with a new tag that exists: what created its new pods, not the fault.
- A `Certificate` in `shop` renewed at T0+5 min, Ready.
- `orders-api` in `shop` runs fine: its pods were created before T0.

## Case w2 — the injector is down and its webhook fails closed

**Question:** "why does the checkout rollout not start in shop"

**Chain:**

1. `Deployment/shop/checkout` rollout: the new ReplicaSet has no pod; `FailedCreate`: "failed calling webhook
   \"inject.mesh.acme.io\": … tls: failed to verify certificate: x509: certificate has expired or is not yet valid".
   A CronJob's Jobs in `shop` fail the same way.
2. → `MutatingWebhookConfiguration/mesh-injector` has `failurePolicy: Fail` and calls `mesh-system/mesh-injector`.
3. → the injector runs and its Service has endpoints: it is reachable, but the API server refuses its certificate.
4. → **cause:** the serving certificate in `Secret/mesh-system/mesh-injector-tls` expired at T0 and was not renewed: its
   cert-manager `Certificate` is not Ready, its issuer `mesh-ca` having been deleted.

*Corrected 2026-11-07, before any fixture:* the first version had the injector crash-looping on its expired certificate.
A server usually keeps serving an expired certificate; it is the API server, as the client, that refuses it.

**Cause object:** `Secret/mesh-system/mesh-injector-tls`, through the injector. **Mechanism:** the injector's serving
certificate expired at T0 and was not renewed, so the API server's calls to the webhook fail TLS verification, and the
webhook, set to `failurePolicy: Fail`, rejects every pod creation in the namespaces it selects.

**Grading:** passes if it names the injector's expired certificate, or the webhook failing closed because its TLS
fails. "A webhook rejects the pods" without why is PARTIAL.

**Decoys:**

- checkout's new version is what the rollout ships: the trigger.
- A `ResourceQuota` in `shop` at 50 %.
- `Deployment/mesh-system/mesh-injector` was last upgraded 40 days ago: not a release fault.

## Case w0 — negative: the same objects, nothing wrong

**Question:** "is something wrong in shop"

The injector upgraded to a chart whose proxy image exists, running with a valid certificate; every pod has its
`mesh-proxy` and runs. The decoys stay: cart-api's redeploy, the certificate renewal, the quota at 50 %.
**Expected outcome:** `no_incident`.

## Size of each case

About 50 objects over `shop`, `mesh-system` and `kube-system`, plus the cluster-scoped
`MutatingWebhookConfiguration`: Deployments, ReplicaSets, pods with their injected containers and annotations,
Services with endpoints, Secrets (metadata only), the Certificate, events, Helm values and manifests of the releases
in question, Loki logs, Prometheus alerts. Every hop gets its entry in `tests/s2/evidence.yaml`.

## What this family can show, and what it cannot

- **What it can show:** whether an arm attributes a fault in many workloads to the one admission webhook they share.
- **What it cannot show:** validating webhooks of policy engines (Kyverno, Gatekeeper), which the product reads as
  `PolicyReport` violations.

## Decided 2026-11-07

1. w1: naming the image without where the container comes from is PARTIAL.
2. w2: the expired certificate, or the webhook failing closed for it, each pass alone; "a webhook rejects the pods"
   without why is PARTIAL.

## Fixtures, 2026-11-07

`tests/s2/build_family_webhook.py` writes w1, w2 and w0 to `tests/s2/cases/` (42 to 49 objects, 3 namespaces and the
cluster-scoped webhook); each hop is checked from `tests/s2/evidence.yaml`. The webhook is in
`kube/mutatingwebhookconfigurations.yaml`, which the test loader does not read yet (loader parity, step 0).
