# S2, family shared ConfigMap — one change, several workloads, not all at once

> Family spec, written 2026-11-07, before any fixture or `expect.json` of this family, under
> `docs/s2-correlation-cases.md`. Status: draft, to approve before the fixtures.

## The family

One ConfigMap is read by several Deployments of different releases. A change to it breaks some of them,
not all: which ones depends on how each reads it (environment variables are read at start, a mounted file
is updated in place) and on when each pod started. The symptom is spread over workloads that share nothing
in their own charts; the cause is one object and one moment.

**What the graph has:** the collector links a pod to a ConfigMap it mounts as a volume (`MOUNTS_CONFIGMAP`,
`ingestion/k8s_collector.py`), but not to one it reads through `envFrom` or `env[].valueFrom`. So c2 has its
edge and c1 does not: the family measures both.

**Sources** (outside the product):

- Kubernetes documentation, ConfigMaps (https://kubernetes.io/docs/concepts/configuration/configmap/): a
  ConfigMap consumed as environment variables is not updated in a running pod, only at its next start; one
  mounted as a volume is updated in place after a delay. Wording checked and quoted when the fixtures are written.
- prometheus-operator/runbooks @ a685d14, `KubePodCrashLooping` ("misconfiguration causing app crash on start").

## Case c1 — an environment value changed; only the pods started since break

**Question:** "why do cart-api and checkout crash in shop"

**Chain:**

1. New pods of `cart-api` and `checkout` crash-loop: "dial tcp: lookup orders-db-v2.data.svc.cluster.local: no such
   host". Older pods of both, and `orders-api`, keep running.
2. → their containers take `DB_HOST` from `ConfigMap/shop/shop-env` (`envFrom`) †.
3. → `shop-env` has `DB_HOST: orders-db-v2.data.svc.cluster.local`; its release `platform-config` was upgraded at T0.
4. → no Service `orders-db-v2` exists in `data`, only `orders-db`. The pods that crash started after T0 (a node drain
   recreated them); those started before still hold the old value.

**Cause object:** `ConfigMap/shop/shop-env`. **Mechanism:** its `DB_HOST` was changed at T0 to a host that does not
exist; pods read it when they start, so only those started since T0 (recreated by a drain) fail.

**Proposed grading:** passes if it names `shop-env`'s `DB_HOST` change. "The database host cannot be resolved" without
the ConfigMap is PARTIAL.

**Decoys:**

- The node drain at T0+20 min: recreated the pods, does not explain the crash.
- `orders-api` reads the same ConfigMap and runs: its pods predate T0 (a control inside the case).
- A `ConfigMap/shop/search-config` change at T0−2 h: unrelated, its consumer runs.

## Case c2 — a mounted file was replaced by an invalid one

**Question:** "why are search-api and web crash-looping in shop"

**Chain:**

1. `search-api` and `web` crash-loop: "failed to load /etc/flags/features.yaml: yaml: line 7: found character that
   cannot start any token". `cart-api` runs.
2. → both mount `ConfigMap/shop/feature-flags` at `/etc/flags` (an edge the graph has); cart-api does not mount it.
3. → `feature-flags` was updated at T0 by its release `feature-flags`; its `features.yaml` has a tab on line 7.
4. → both services reload the file on change, fail, exit, and fail again on start.

**Cause object:** `ConfigMap/shop/feature-flags`. **Mechanism:** its `features.yaml` was replaced at T0 by an invalid
YAML file (a tab on line 7); the two services that mount it reload it, exit, and fail on every start.

**Proposed grading:** passes if it names the `feature-flags` ConfigMap's invalid content. "A configuration file fails
to parse" without the ConfigMap is PARTIAL.

**Decoys:**

- `web` was redeployed at T0−1 h with a new tag: before the change, and its pods ran until T0.
- `cart-api`'s CPU throttling alert: unrelated.
- The `feature-flags` release also changed another flag's value (visible in the previous manifest): valid, harmless.

## Case c0 — negative: the same objects, nothing wrong

**Question:** "is something wrong in shop"

`shop-env` points to `orders-db`, `feature-flags` holds valid YAML, every pod runs. The decoys stay: the old drain, the
`search-config` change, web's redeploy, the throttling alert. **Expected outcome:** `no_incident`.

## Size of each case

About 50 objects over `shop`, `data` and `kube-system`: Deployments, ReplicaSets with selectors, pods with their
`envFrom` and volumes, the ConfigMaps (now built by the product's collector, step 0), Services with endpoints, events,
Helm history and manifests of the config release, Loki logs, Prometheus alerts. Every hop gets its entry in
`tests/s2/evidence.yaml`.

## What this family can show, and what it cannot

- **What it can show:** whether an arm joins crashes in several releases to the one ConfigMap they share, and uses
  the start times to explain why some pods break and others do not. c1 and c2 compare a link the graph lacks
  (`envFrom`) with one it has (a volume).
- **What it cannot show:** Secrets consumed the same way (their values are never read by the product), or external
  configuration stores.

## Decided 2026-11-07

1. c1: "the database host cannot be resolved" without the ConfigMap is PARTIAL.
2. c2: "a configuration file fails to parse" without the ConfigMap is PARTIAL.
3. c2's third decoy, the other flag changed by the same release, is shown by the previous manifest
   (`helm/manifest-previous.yaml`), so it is visible and not only stated.

## Fixtures, 2026-11-07

`tests/s2/build_family_configmap.py` writes c1, c2 and c0 to `tests/s2/cases/`; each hop is checked from
`tests/s2/evidence.yaml`. Built with the product's collectors (step 0), the graph links search-api's and web's pods to
`feature-flags` and `search-config` (volumes) and links no pod to `shop-env` (`envFrom`): the contrast the family
was written for.
