You are grading answers to Kubernetes incident questions. Each answer was written by an automated system; several
systems are mixed, in random order, and you are not told which wrote what.

For each item you get the question, the ground truth of the incident (the object at fault and how it causes the
symptom, or "No incident"), what an answer must name to pass, and what earns only PARTIAL. Grade the substance only:
different wording never costs points and matching words never earn them.

- YES: it names the object at fault (or one the ground truth accepts) and the mechanism, as "Passes if" says.
- PARTIAL: it is on the path to the cause but stops short, as "Partial if only" describes, or it names the right
  object with a wrong mechanism.
- NO: anything else, including naming a fault when the ground truth is "No incident", and saying "No incident" when
  there is one.

If you hesitate between two verdicts, choose the stricter. Grade each item on its own; do not guess which system wrote
it; do not change a verdict once given. Output only a table, one row per item: | item | verdict | reason in at most
15 words |

There are 147 items.

---

Item 1
Question: why are search-api and web crash-looping in shop
Ground truth: ConfigMap/shop/feature-flags: its features.yaml was replaced at T0 by invalid YAML (a tab on line 7); search-api and web, which mount it, reload it, exit, and fail again on every start
Passes if: names the feature-flags ConfigMap's invalid content
Partial if only: a configuration file fails to parse, without the ConfigMap
Answer: The root cause is a YAML syntax error in the `features.yaml` ConfigMap, which is causing the search-api and web applications to fail to load their configuration and crash.

Item 2
Question: why doesn't api scale up under load in shop
Ground truth: APIService/v1beta1.custom.metrics.k8s.io: the APIService still points to monitoring/prometheus-adapter, which has had no endpoints since the adapter moved to observability at T0, so the custom metrics API is unavailable and the HPA cannot read http_requests_per_second
Passes if: names the APIService, or the custom metrics API, pointing to the adapter's old location
Partial if only: the HPA cannot read its metric, or the metrics API is unavailable, without where it points
Answer: The root cause is the HorizontalPodAutoscaler's inability to fetch metrics from the custom metrics API, which is causing it to fail scaling up. This is evident from the warning event involving the HorizontalPodAutoscaler and the firing Prometheus alert about high latency.

Item 3
Question: why does checkout-api fail to reserve stock in shop
Ground truth: Service/platform/inventory: the inventory upgrade moved the container to port 9090, while Service platform/inventory, owned by another release, still targets 8080: every connection is refused while the pods report Ready
Passes if: names the port mismatch between the Service and the inventory pods, blaming either side
Partial if only: inventory unreachable or connection refused, without the port
Answer: The root cause is likely a connectivity issue between the checkout-api and the inventory service in the platform namespace. The repeated error logs mention a connection refused error when trying to dial the inventory service's IP address. This, combined with the high CPU throttling on the checkout-api container, suggests that the container is spending a significant amount of time attempting to connect to the inventory service.

Item 4
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: pricing drifted from its declared configuration: spec.replicas is declared 3 but deployed 5. It was changed outside Helm after the release was applied.

Item 5
Question: is something wrong with checkout-api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause is likely a misconfiguration or issue with the checkout-api container within the pod, causing it to become unhealthy and experience high CPU throttling.

Item 6
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The drift in the `container.cart-api.image` and `container.billing-worker.image` fields of the Deployment/shop/cart-api and Deployment/shop/billing-worker resources, respectively, have led to the observed drift. Additionally, the incorrect `externalTrafficPolicy` and `terminationMessagePolicy` settings in the Service/shop/billing-worker and Pod/shop/billing-worker-84b7c6d5f-04d9e and Pod/shop/billing-worker-84b7c6d5f-06c8d resources are causing issues.

Item 7
Question: why are search-api and web crash-looping in shop
Ground truth: ConfigMap/shop/feature-flags: its features.yaml was replaced at T0 by invalid YAML (a tab on line 7); search-api and web, which mount it, reload it, exit, and fail again on every start
Passes if: names the feature-flags ConfigMap's invalid content
Partial if only: a configuration file fails to parse, without the ConfigMap
Answer: The root cause is likely a misconfiguration in the `/etc/flags/features.yaml` file for the search-api and web pods. This file is causing the pods to fail to load, resulting in the CrashLoopBackOff condition.

Item 8
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The high CPU usage in the 'search-api' container is causing the Kubernetes node to throttle its CPU resources, as indicated by the Prometheus alert.

Item 9
Question: why are search-api and web crash-looping in shop
Ground truth: ConfigMap/shop/feature-flags: its features.yaml was replaced at T0 by invalid YAML (a tab on line 7); search-api and web, which mount it, reload it, exit, and fail again on every start
Passes if: names the feature-flags ConfigMap's invalid content
Partial if only: a configuration file fails to parse, without the ConfigMap
Answer: The root cause is a YAML parsing error in the `features.yaml` ConfigMap, which is being used by the search-api and web applications. This error is causing the containers to crash and enter a loop of restarting and crashing.

Item 10
Question: why does api take so long to absorb traffic peaks in shop
Ground truth: HorizontalPodAutoscaler/shop/api: the T0 release (chart 3.2.0) set behavior.scaleUp to one pod per 600 s with a 600 s stabilization window, so the HPA adds replicas too slowly and api saturates at each peak
Passes if: names the HPA's scale-up policy or rate limit
Partial if only: the HPA scales too slowly, without the policy; the release, without the policy
Answer: The root cause is the deployment's replicas count being set to 1, which is not enough to handle the traffic peaks in the shop namespace.

Item 11
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The `spec.minReadySeconds` for the `cart-api` deployment is set to `0`, which means that the pods are considered ready as soon as they are created, even if they are not yet fully initialized. This can lead to high CPU usage due to uninitialized processes.

Item 12
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The drift in the container image version of the cart-api deployment is the root cause. The deployment was edited outside of Helm, resulting in the observed drift.

Item 13
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause is that the "cart-api" and "checkout" deployments are paused, which is contrary to the expected operational state. This is indicated by the Helm release "mesh-injector" in the "shop" namespace, which is deployed and active.

Item 14
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The deployment of the cart-api and checkout-api services is paused, as indicated by the `spec.paused: true` setting in the Deployment resources. This is a drift from the Helm chart's default setting of `false`.

Item 15
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause appears to be an unintended change in the image version of the cart-api deployment, which may be causing increased resource usage, leading to high CPU throttling in the monitoring namespace.

Item 16
Question: why does search-api not scale up in shop
Ground truth: CronJob/shop/report-export (origin: Service/data/reports-db): report-export starts a Job every 10 minutes with concurrencyPolicy Allow; each waits on reports-db, which has had no ready endpoint since T0, so none finishes and 25 Jobs fill the namespace's pods quota
Passes if: names CronJob/shop/report-export and the mechanism
Partial if only: Service/data/reports-db
Answer: The root cause is the exceeded quota for the namespace shop in terms of pods. The namespace shop is using 100% of its pods quota, which is limiting the creation of new pods for the search-api deployment.

Item 17
Question: why are new pods of cart-api and web stuck in shop
Ground truth: Deployment/mesh-system/mesh-injector: since its T0 upgrade (chart 1.21.0) the injector injects ghcr.io/acme/mesh-proxy:1.21.0-rc, a tag that does not exist, into every new pod of the namespaces its webhook selects
Passes if: names the injector or its webhook as the source of the mesh-proxy container with the bad image
Partial if only: an image cannot be pulled, without where the container comes from
Answer: The root cause is the image "ghcr.io/acme/mesh-proxy:1.21.0-rc" not being found, which is causing the pods to fail to start. This is evident from the Warning events stating "Failed to pull image" and "BackOff pulling image".

Item 18
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The Deployment/monitoring/alertmanager is paused, which is contrary to the Helm declared values. The deployment is set to `paused: false`, but the deployment status shows it is paused.

Item 19
Question: why doesn't api scale up under load in shop
Ground truth: APIService/v1beta1.custom.metrics.k8s.io: the APIService still points to monitoring/prometheus-adapter, which has had no endpoints since the adapter moved to observability at T0, so the custom metrics API is unavailable and the HPA cannot read http_requests_per_second
Passes if: names the APIService, or the custom metrics API, pointing to the adapter's old location
Partial if only: the HPA cannot read its metric, or the metrics API is unavailable, without where it points
Answer: The root cause is likely that the Deployment/shop/api is paused, preventing it from scaling up. This is evidenced by the `spec.paused: false` drift in the Helm release.

Item 20
Question: why does orders-api keep restarting in shop
Ground truth: Deployment/shop/orders-api: the orders-api chart upgrade to 3.0.0 dropped the app=orders-api pod label, so NetworkPolicy data/allow-orders-to-db no longer selects its pods and data/default-deny-ingress drops their connections to orders-db
Passes if: names the pod label change against the policy's podSelector
Partial if only: the upgrade without the label; the policy without the label
Answer: The persistent database connection issue is causing the orders-api pods to restart. The pods are marked as CrashLoopBackOff, indicating they are failing to connect to the database and are being restarted by the Kubernetes controller.

Item 21
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The Deployment/shop/cart-api and Deployment/shop/checkout are both set to `paused: false`, which means they are actively rolling out updates. The high CPU throttling could be due to the `maxSurge` and `maxUnavailable` settings, which are both set to `25%`. This could lead to uneven distribution of pods and resource contention, causing the CPU throttling.

Item 22
Question: why does checkout-api fail to reserve stock in shop
Ground truth: Service/platform/inventory: the inventory upgrade moved the container to port 9090, while Service platform/inventory, owned by another release, still targets 8080: every connection is refused while the pods report Ready
Passes if: names the port mismatch between the Service and the inventory pods, blaming either side
Partial if only: inventory unreachable or connection refused, without the port
Answer: Insufficient evidence: fault signals exist but no rule explains them.

Item 23
Question: why is the search-api rollout stuck in shop
Ground truth: LimitRange/shop/container-defaults: its maximum memory per container was lowered to 1Gi at T0, below search-api's 2Gi limit, so admission refuses every new pod of the rollout while the old pods keep running
Passes if: names the LimitRange's maximum against search-api's limit
Partial if only: admission refuses the pods, or the limit is too high, without the LimitRange
Answer: The Deployment/search-api is stuck in the Progressing state due to a resource limit violation. The resource limit for memory per container is set to 1Gi, but the maximum allowed limit is 2Gi. This is causing the deployment to fail and remain in the Progressing state.

Item 24
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: The Deployment/platform/pricing has drifted, with the spec.replicas field set to 3 instead of the expected 5. This misconfiguration causes the deployment to scale down below the desired number of replicas, leading to the observed errors.

Item 25
Question: why does report-worker keep crashing in shop
Ground truth: LimitRange/shop/container-defaults: its default memory limit (256Mi), created at T0, was set by admission on report-worker's pods recreated by a node drain; report-worker declares no limit and needs about 700Mi, so it is OOMKilled
Passes if: names the LimitRange's default as the source of the 256Mi limit
Partial if only: the memory limit is too low, without the LimitRange
Answer: The report-worker deployment is experiencing OOMKills and high CPU throttling, which are likely caused by insufficient resources (CPU or memory) in the shop namespace.

Item 26
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: The root cause is the increased error rate and high latency of the pricing service, which is causing the checkout-api to fail. This is indicated by the Prometheus alerts for HighErrorRate and HighLatencyP95 in the shop namespace. The error rate is 38%, and the p95 latency is 410ms.

Item 27
Question: why does api take so long to absorb traffic peaks in shop
Ground truth: HorizontalPodAutoscaler/shop/api: the T0 release (chart 3.2.0) set behavior.scaleUp to one pod per 600 s with a 600 s stabilization window, so the HPA adds replicas too slowly and api saturates at each peak
Passes if: names the HPA's scale-up policy or rate limit
Partial if only: the HPA scales too slowly, without the policy; the release, without the policy
Answer: Insufficient evidence: fault signals exist but no rule explains them.

Item 28
Question: is something wrong with checkout-api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause is likely a misconfiguration or resource exhaustion within the checkout-api container, leading to high CPU usage and the pod becoming unhealthy. The unhealthy status is indicated by the failed readiness probe, and the high CPU throttling is confirmed by the Prometheus alert.

Item 29
Question: is something wrong with api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The deployment for the shop/api service is paused and has a `minReadySeconds` value of 0, which is causing it to not be considered available by Kubernetes.

Item 30
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The drift in the `container.cart-api.image` and `container.billing-worker.image` fields of the Deployment/shop/cart-api and Deployment/shop/billing-worker resources, respectively, have led to the observed misconfiguration. This drift is indicated by the Helm declared vs observed drift event.

Item 31
Question: why are checkout-api's payments failing in shop
Ground truth: Namespace/shop: Namespace shop's team label changed from storefront to shop, so NetworkPolicy pay/allow-from-storefront no longer matches it and pay/default-deny-ingress drops checkout-api's calls to payments
Passes if: names the label mismatch, through the Namespace or the policy
Partial if only: a NetworkPolicy without the label mismatch
Answer: The root cause appears to be a network issue between the checkout-api pods and the payments service. The repeated i/o timeout errors in the logs suggest that the pods are unable to establish a connection to the payments service.

Item 32
Question: why are new pods of cart-api and web stuck in shop
Ground truth: Deployment/mesh-system/mesh-injector: since its T0 upgrade (chart 1.21.0) the injector injects ghcr.io/acme/mesh-proxy:1.21.0-rc, a tag that does not exist, into every new pod of the namespaces its webhook selects
Passes if: names the injector or its webhook as the source of the mesh-proxy container with the bad image
Partial if only: an image cannot be pulled, without where the container comes from
Answer: The root cause is a failed image pull for the mesh-proxy image (ghcr.io/acme/mesh-proxy:1.21.0-rc) and insufficient ephemeral storage on the worker-node-02.

Item 33
Question: why do cart-api and checkout crash in shop
Ground truth: ConfigMap/shop/shop-env: its DB_HOST was changed at T0 to orders-db-v2, which does not exist; pods read it when they start, so only those recreated since T0 by a node drain fail, while older pods keep the old value
Passes if: names shop-env's DB_HOST change
Partial if only: the database host cannot be resolved, without the ConfigMap
Answer: The root cause is a DNS resolution issue where the cart-api and checkout services are unable to resolve the orders-db-v2 service's hostname, resulting in connection failures. This issue is evident from the repeated error logs in the context.

Item 34
Question: why does api take so long to absorb traffic peaks in shop
Ground truth: HorizontalPodAutoscaler/shop/api: the T0 release (chart 3.2.0) set behavior.scaleUp to one pod per 600 s with a 600 s stabilization window, so the HPA adds replicas too slowly and api saturates at each peak
Passes if: names the HPA's scale-up policy or rate limit
Partial if only: the HPA scales too slowly, without the policy; the release, without the policy
Answer: The `Deployment/shop/api` is paused, which is preventing it from scaling up to handle traffic peaks.

Item 35
Question: why does api take so long to absorb traffic peaks in shop
Ground truth: HorizontalPodAutoscaler/shop/api: the T0 release (chart 3.2.0) set behavior.scaleUp to one pod per 600 s with a 600 s stabilization window, so the HPA adds replicas too slowly and api saturates at each peak
Passes if: names the HPA's scale-up policy or rate limit
Partial if only: the HPA scales too slowly, without the policy; the release, without the policy
Answer: The root cause is likely a misconfiguration in the `shop/api` deployment, specifically the `spec.strategy.rollingUpdate` setting, which is set to `RollingUpdate`. This setting may be causing unnecessary delays in pod startup and scaling, leading to longer response times during traffic peaks.

Item 36
Question: why does orders-api keep restarting in shop
Ground truth: Deployment/shop/orders-api: the orders-api chart upgrade to 3.0.0 dropped the app=orders-api pod label, so NetworkPolicy data/allow-orders-to-db no longer selects its pods and data/default-deny-ingress drops their connections to orders-db
Passes if: names the pod label change against the policy's podSelector
Partial if only: the upgrade without the label; the policy without the label
Answer: The orders-api pods are unable to connect to the orders-db database, causing them to crash and enter a CrashLoopBackOff state. This is likely due to a network issue or incorrect database connection configuration.

Item 37
Question: why are checkout-api's payments failing in shop
Ground truth: Namespace/shop: Namespace shop's team label changed from storefront to shop, so NetworkPolicy pay/allow-from-storefront no longer matches it and pay/default-deny-ingress drops checkout-api's calls to payments
Passes if: names the label mismatch, through the Namespace or the policy
Partial if only: a NetworkPolicy without the label mismatch
Answer: The root cause appears to be an i/o timeout when attempting to dial the payments service at `payments.pay.svc.cluster.local:8443`.

Item 38
Question: why is the search-api rollout stuck in shop
Ground truth: LimitRange/shop/container-defaults: its maximum memory per container was lowered to 1Gi at T0, below search-api's 2Gi limit, so admission refuses every new pod of the rollout while the old pods keep running
Passes if: names the LimitRange's maximum against search-api's limit
Partial if only: admission refuses the pods, or the limit is too high, without the LimitRange
Answer: The maximum memory usage per container in the ReplicaSet is exceeding the limit set.

Item 39
Question: is something wrong with api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The `shop/api` deployment is paused, which is likely the root cause of the issue. This is indicated by the `paused=true` setting in the deployment's spec, which is a default setting for new deployments.

Item 40
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: No incident: no fault signal in the evidence.

Item 41
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The declared replica count for the cart-api deployment is 1, but the actual number of replicas running is less than expected. This discrepancy could be due to a failed deployment, a scaling issue, or a manual intervention.

Item 42
Question: why are checkout-api's payments failing in shop
Ground truth: Namespace/shop: Namespace shop's team label changed from storefront to shop, so NetworkPolicy pay/allow-from-storefront no longer matches it and pay/default-deny-ingress drops checkout-api's calls to payments
Passes if: names the label mismatch, through the Namespace or the policy
Partial if only: a NetworkPolicy without the label mismatch
Answer: The root cause is likely a failure in the upstream service `payments.pay.svc.cluster.local`, as indicated by the repeated failure of readiness probes for the checkout-api pods. The readiness probes are failing with a statuscode of 503, which typically indicates a network or service issue.

Item 43
Question: why are new pods of cart-api and web stuck in shop
Ground truth: Deployment/mesh-system/mesh-injector: since its T0 upgrade (chart 1.21.0) the injector injects ghcr.io/acme/mesh-proxy:1.21.0-rc, a tag that does not exist, into every new pod of the namespaces its webhook selects
Passes if: names the injector or its webhook as the source of the mesh-proxy container with the bad image
Partial if only: an image cannot be pulled, without where the container comes from
Answer: The root cause is the image "ghcr.io/acme/mesh-proxy:1.21.0-rc" not being found, as indicated by the Warning events for both cart-api and web pods.

Item 44
Question: why do cart-api and checkout crash in shop
Ground truth: ConfigMap/shop/shop-env: its DB_HOST was changed at T0 to orders-db-v2, which does not exist; pods read it when they start, so only those recreated since T0 by a node drain fail, while older pods keep the old value
Passes if: names shop-env's DB_HOST change
Partial if only: the database host cannot be resolved, without the ConfigMap
Answer: The database connectivity issues are causing the pods to crash. The pods are configured to use the `orders-db-v2.data.svc.cluster.local` database, but the service is not resolving this hostname.

Item 45
Question: why does search-api not scale up in shop
Ground truth: CronJob/shop/report-export (origin: Service/data/reports-db): report-export starts a Job every 10 minutes with concurrencyPolicy Allow; each waits on reports-db, which has had no ready endpoint since T0, so none finishes and 25 Jobs fill the namespace's pods quota
Passes if: names CronJob/shop/report-export and the mechanism
Partial if only: Service/data/reports-db
Answer: The container billing-worker of billing-worker-84b7c6d5f-04d9e exceeds its memory limit (OOMKilled). The ResourceQuota shop-quota of namespace shop is exhausted: the replicaset controller cannot create the pods it needs (exceeded quota).

Item 46
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The declared replica count for the cart-api deployment is 1, but no pods are currently running. Additionally, the image pull policy for the cart-api container is set to IfNotPresent, which may cause issues if the image is not already present in the cluster.

Item 47
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The deployment of the cart-api and checkout-api services are paused, as indicated by the `spec.paused: true` setting in their respective deployments. This is a drift from the expected state where `spec.paused: false` is the default.

Item 48
Question: why are checkout-api's payments failing in shop
Ground truth: Namespace/shop: Namespace shop's team label changed from storefront to shop, so NetworkPolicy pay/allow-from-storefront no longer matches it and pay/default-deny-ingress drops checkout-api's calls to payments
Passes if: names the label mismatch, through the Namespace or the policy
Partial if only: a NetworkPolicy without the label mismatch
Answer: The root cause is likely a failure in the upstream payment service (payments.pay.svc.cluster.local:8443), as evidenced by the repeated HTTP probe failures. The readiness probes are failing with a statuscode of 503, indicating that the service is not reachable.

Item 49
Question: why do cart-api and checkout crash in shop
Ground truth: ConfigMap/shop/shop-env: its DB_HOST was changed at T0 to orders-db-v2, which does not exist; pods read it when they start, so only those recreated since T0 by a node drain fail, while older pods keep the old value
Passes if: names shop-env's DB_HOST change
Partial if only: the database host cannot be resolved, without the ConfigMap
Answer: Insufficient evidence: fault signals exist but no rule explains them.

Item 50
Question: why does orders-api keep restarting in shop
Ground truth: Deployment/shop/orders-api: the orders-api chart upgrade to 3.0.0 dropped the app=orders-api pod label, so NetworkPolicy data/allow-orders-to-db no longer selects its pods and data/default-deny-ingress drops their connections to orders-db
Passes if: names the pod label change against the policy's podSelector
Partial if only: the upgrade without the label; the policy without the label
Answer: The persistent database connection issues are causing the orders-api pods to restart. The pods are configured to use a database service named orders-db.data.svc.cluster.local, which is failing to connect due to an i/o timeout. This is evident from the repeated error logs and the BackOff events.

Item 51
Question: why does api take so long to absorb traffic peaks in shop
Ground truth: HorizontalPodAutoscaler/shop/api: the T0 release (chart 3.2.0) set behavior.scaleUp to one pod per 600 s with a 600 s stabilization window, so the HPA adds replicas too slowly and api saturates at each peak
Passes if: names the HPA's scale-up policy or rate limit
Partial if only: the HPA scales too slowly, without the policy; the release, without the policy
Answer: The `PodDisruptionBudget` and `HorizontalPodAutoscaler` configurations for the `api` deployment in the `shop` namespace are set to allow for more pod disruptions and scaling down than necessary, resulting in insufficient resources during traffic peaks.

Item 52
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The `shop/cart-api` and `shop/checkout` deployments are paused and have zero replicas, suggesting a Helm deployment issue. The Helm release `mesh-injector` in the `shop` namespace is the root cause, as it is the only Helm release listed and it is in a `deployed` status.

Item 53
Question: why does report-worker keep crashing in shop
Ground truth: LimitRange/shop/container-defaults: its default memory limit (256Mi), created at T0, was set by admission on report-worker's pods recreated by a node drain; report-worker declares no limit and needs about 700Mi, so it is OOMKilled
Passes if: names the LimitRange's default as the source of the 256Mi limit
Partial if only: the memory limit is too low, without the LimitRange
Answer: The `report-worker` Pod is consuming more resources than available, leading to OOMKills and high CPU throttling. This could be due to insufficient resource requests or limits set in the deployment or pod configuration.

Item 54
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The `spec.minReadySeconds` for the `cart-api` deployment is set to `0`, which means that the pods are considered ready immediately upon creation, even if they are not yet fully initialized. This can lead to high CPU usage as the pods start up and perform their initialization tasks.

Item 55
Question: why does checkout-api fail to reserve stock in shop
Ground truth: Service/platform/inventory: the inventory upgrade moved the container to port 9090, while Service platform/inventory, owned by another release, still targets 8080: every connection is refused while the pods report Ready
Passes if: names the port mismatch between the Service and the inventory pods, blaming either side
Partial if only: inventory unreachable or connection refused, without the port
Answer: The root cause appears to be a connection issue between the `checkout-api` and the `inventory` service in the `platform` namespace. The repeated error logs indicate that the `checkout-api` is unable to connect to the `inventory` service, which is causing the readiness probe to fail and the high error rate. The CPU throttling may be a result of the `checkout-api` trying to compensate for the failed connections.

Item 56
Question: why can't billing-worker roll out in shop
Ground truth: Deployment/shop/catalog-api: catalog-api's live CPU request (100m) is below its applied 500m, so its HPA reads five times the utilization, scales it to its maximum of 20, and those replicas' 1Gi memory requests fill the namespace's requests.memory quota
Passes if: names Deployment/shop/catalog-api and the mechanism
Partial if only: the right object with a wrong mechanism
Answer: The billing-worker deployment is requesting 2Gi of memory, but the shop namespace has a quota of 32Gi, and it is currently using 31Gi.

Item 57
Question: is something wrong with checkout-api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause is likely a misconfiguration in the `checkout-api` deployment, specifically the `imagePullPolicy` set to `IfNotPresent`, which may cause the pod to pull the image every time, leading to the failure of the readiness probe.

Item 58
Question: why do cart-api and checkout crash in shop
Ground truth: ConfigMap/shop/shop-env: its DB_HOST was changed at T0 to orders-db-v2, which does not exist; pods read it when they start, so only those recreated since T0 by a node drain fail, while older pods keep the old value
Passes if: names shop-env's DB_HOST change
Partial if only: the database host cannot be resolved, without the ConfigMap
Answer: The root cause is a DNS resolution issue preventing the cart-api and checkout services from connecting to the orders-db-v2 database. This is evident from the repeated error messages in the logs.

Item 59
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The container billing-worker of billing-worker-84b7c6d5f-04d9e exceeds its memory limit (OOMKilled).

Item 60
Question: why are search-api and web crash-looping in shop
Ground truth: ConfigMap/shop/feature-flags: its features.yaml was replaced at T0 by invalid YAML (a tab on line 7); search-api and web, which mount it, reload it, exit, and fail again on every start
Passes if: names the feature-flags ConfigMap's invalid content
Partial if only: a configuration file fails to parse, without the ConfigMap
Answer: The root cause is a YAML syntax error in the `features.yaml` ConfigMap, which is causing the search-api and web containers to fail to load their configuration.

Item 61
Question: why can't billing-worker roll out in shop
Ground truth: Deployment/shop/catalog-api: catalog-api's live CPU request (100m) is below its applied 500m, so its HPA reads five times the utilization, scales it to its maximum of 20, and those replicas' 1Gi memory requests fill the namespace's requests.memory quota
Passes if: names Deployment/shop/catalog-api and the mechanism
Partial if only: the right object with a wrong mechanism
Answer: The root cause is the resource quota limit on the shop namespace, which is preventing the deployment from scaling up to the desired number of replicas.

Item 62
Question: is something wrong with checkout-api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The `checkout-api` deployment in the `shop` namespace is paused (`paused: true`), which is likely causing the readiness probe failures. This is evidenced by the `Deployment/shop/cart-api` resource.

Item 63
Question: why doesn't api scale up under load in shop
Ground truth: APIService/v1beta1.custom.metrics.k8s.io: the APIService still points to monitoring/prometheus-adapter, which has had no endpoints since the adapter moved to observability at T0, so the custom metrics API is unavailable and the HPA cannot read http_requests_per_second
Passes if: names the APIService, or the custom metrics API, pointing to the adapter's old location
Partial if only: the HPA cannot read its metric, or the metrics API is unavailable, without where it points
Answer: The root cause is the HorizontalPodAutoscaler's inability to fetch metrics from the custom metrics API, which is causing it to fail scaling up. This is evident from the Warning event and the firing Prometheus alert.

Item 64
Question: why can't billing-worker roll out in shop
Ground truth: Deployment/shop/catalog-api: catalog-api's live CPU request (100m) is below its applied 500m, so its HPA reads five times the utilization, scales it to its maximum of 20, and those replicas' 1Gi memory requests fill the namespace's requests.memory quota
Passes if: names Deployment/shop/catalog-api and the mechanism
Partial if only: the right object with a wrong mechanism
Answer: Deployment/shop/billing-worker is paused=true, preventing rollouts. This is due to a quota exceeded error.

Item 65
Question: why does orders-api keep restarting in shop
Ground truth: Deployment/shop/orders-api: the orders-api chart upgrade to 3.0.0 dropped the app=orders-api pod label, so NetworkPolicy data/allow-orders-to-db no longer selects its pods and data/default-deny-ingress drops their connections to orders-db
Passes if: names the pod label change against the policy's podSelector
Partial if only: the upgrade without the label; the policy without the label
Answer: Insufficient evidence: fault signals exist but no rule explains them.

Item 66
Question: why does search-api not scale up in shop
Ground truth: CronJob/shop/report-export (origin: Service/data/reports-db): report-export starts a Job every 10 minutes with concurrencyPolicy Allow; each waits on reports-db, which has had no ready endpoint since T0, so none finishes and 25 Jobs fill the namespace's pods quota
Passes if: names CronJob/shop/report-export and the mechanism
Partial if only: Service/data/reports-db
Answer: The search-api deployment is unable to create new replicas because it has exceeded the shop namespace's pods quota.

Item 67
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The cart-api deployment is paused, which halts rollouts and prevents new pods from being created. This was likely done accidentally.

Item 68
Question: why can't billing-worker roll out in shop
Ground truth: Deployment/shop/catalog-api: catalog-api's live CPU request (100m) is below its applied 500m, so its HPA reads five times the utilization, scales it to its maximum of 20, and those replicas' 1Gi memory requests fill the namespace's requests.memory quota
Passes if: names Deployment/shop/catalog-api and the mechanism
Partial if only: the right object with a wrong mechanism
Answer: The container billing-worker of billing-worker-84b7c6d5f-04d9e exceeds its memory limit (OOMKilled). The ResourceQuota shop-quota of namespace shop is exhausted: the replicaset controller cannot create the pods it needs (exceeded quota).

Item 69
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause is that both `shop/cart-api` and `shop/checkout-api` deployments are paused, as indicated by the `paused: true` setting in their respective deployment YAML files. This setting prevents the pods from being scheduled and thus no traffic is reaching the services.

Item 70
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The Deployment/monitoring/alertmanager is paused, which could be causing the observed CPU throttling issues. The alertmanager deployment is paused, preventing it from scaling up and potentially causing resource contention.

Item 71
Question: is something wrong with checkout-api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause is likely a misconfiguration or issue with the checkout-api container within the pod, causing it to become unhealthy and experience high CPU throttling.

Item 72
Question: is something wrong with api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The `shop/api` deployment is paused, which is likely causing issues. The deployment's `paused` field is set to `true`, which can prevent rollouts and cause pods to be unavailable.

Item 73
Question: why does checkout-api fail to reserve stock in shop
Ground truth: Service/platform/inventory: the inventory upgrade moved the container to port 9090, while Service platform/inventory, owned by another release, still targets 8080: every connection is refused while the pods report Ready
Passes if: names the port mismatch between the Service and the inventory pods, blaming either side
Partial if only: inventory unreachable or connection refused, without the port
Answer: The Pod/shop/cart-api-5d6f8c7b9-cc8c8 and Pod/shop/cart-api-5d6f8c7b9-ce7b7 Pods are experiencing connection issues with the inventory service, as evidenced by the error logs and the Kubernetes Event indicating a readiness probe failure.

Item 74
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: The root cause is the overloading of the pricing deployment due to a cache write failure, which in turn is causing the pricing service to return 503 errors to the checkout-api.

Item 75
Question: why can't billing-worker roll out in shop
Ground truth: Deployment/shop/catalog-api: catalog-api's live CPU request (100m) is below its applied 500m, so its HPA reads five times the utilization, scales it to its maximum of 20, and those replicas' 1Gi memory requests fill the namespace's requests.memory quota
Passes if: names Deployment/shop/catalog-api and the mechanism
Partial if only: the right object with a wrong mechanism
Answer: The billing-worker deployment is requesting 2 GiB of memory, but the shop namespace has a quota of 32 GiB for memory requests, and it is currently using 31 GiB.

Item 76
Question: why do cart-api and checkout crash in shop
Ground truth: ConfigMap/shop/shop-env: its DB_HOST was changed at T0 to orders-db-v2, which does not exist; pods read it when they start, so only those recreated since T0 by a node drain fail, while older pods keep the old value
Passes if: names shop-env's DB_HOST change
Partial if only: the database host cannot be resolved, without the ConfigMap
Answer: The root cause is likely a misconfiguration in the database connection settings for the cart-api and checkout services. The drift in the `values.yaml` file for the `platform-config` Helm release indicates that the `dbHost` value is set to `orders-db-v2.data.svc.cluster.local`, which does not exist. This misconfiguration causes the services to fail to connect to the database, leading to the observed CrashLoopBackOff and fatal database connection errors.

Item 77
Question: why are checkout-api's payments failing in shop
Ground truth: Namespace/shop: Namespace shop's team label changed from storefront to shop, so NetworkPolicy pay/allow-from-storefront no longer matches it and pay/default-deny-ingress drops checkout-api's calls to payments
Passes if: names the label mismatch, through the Namespace or the policy
Partial if only: a NetworkPolicy without the label mismatch
Answer: The root cause is a network issue with the payments service, indicated by the repeated "Readiness probe failed" events and the error messages in the logs. The readiness probe is failing with a statuscode of 503, which suggests a network timeout.

Item 78
Question: why are search-api and web crash-looping in shop
Ground truth: ConfigMap/shop/feature-flags: its features.yaml was replaced at T0 by invalid YAML (a tab on line 7); search-api and web, which mount it, reload it, exit, and fail again on every start
Passes if: names the feature-flags ConfigMap's invalid content
Partial if only: a configuration file fails to parse, without the ConfigMap
Answer: Insufficient evidence: fault signals exist but no rule explains them.

Item 79
Question: why does search-api not scale up in shop
Ground truth: CronJob/shop/report-export (origin: Service/data/reports-db): report-export starts a Job every 10 minutes with concurrencyPolicy Allow; each waits on reports-db, which has had no ready endpoint since T0, so none finishes and 25 Jobs fill the namespace's pods quota
Passes if: names CronJob/shop/report-export and the mechanism
Partial if only: Service/data/reports-db
Answer: The deployment of search-api in the shop namespace is constrained by a quota limit, preventing it from scaling up beyond the current limit of 40 pods. This is indicated by the drift in the `spec.replicas` field from the Helm chart, which is set to 1, and the associated event that reports the quota limit exceeded.

Item 80
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The Deployment/monitoring/alertmanager is paused, which is contrary to the Helm chart's default setting of `false`. This could be causing the observed CPU throttling.

Item 81
Question: is something wrong with api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The `shop/api` deployment is paused, which is likely causing the observed issue. The `paused=true` setting in the deployment spec prevents rollouts and could lead to availability problems.

Item 82
Question: why are checkout-api's payments failing in shop
Ground truth: Namespace/shop: Namespace shop's team label changed from storefront to shop, so NetworkPolicy pay/allow-from-storefront no longer matches it and pay/default-deny-ingress drops checkout-api's calls to payments
Passes if: names the label mismatch, through the Namespace or the policy
Partial if only: a NetworkPolicy without the label mismatch
Answer: Insufficient evidence: fault signals exist but no rule explains them.

Item 83
Question: why is the search-api rollout stuck in shop
Ground truth: LimitRange/shop/container-defaults: its maximum memory per container was lowered to 1Gi at T0, below search-api's 2Gi limit, so admission refuses every new pod of the rollout while the old pods keep running
Passes if: names the LimitRange's maximum against search-api's limit
Partial if only: admission refuses the pods, or the limit is too high, without the LimitRange
Answer: The Deployment/search-api is stuck in a rollout due to resource limits exceeding the specified maximum memory usage. The Deployment/monitoring/alertmanager resource has a maximum memory limit of 2Gi, but the Deployment/search-api has a maximum memory limit of 1Gi, causing the error.

Item 84
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause appears to be an unintentional change in the image version of the cart-api deployment, which may be causing increased resource usage leading to high CPU throttling in the monitoring namespace.

Item 85
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The `cart-api` deployment has a higher number of replicas than necessary, causing increased resource consumption and CPU throttling. This is indicated by the declared `spec.replicas: 1` but the actual number of replicas is not provided in the context.

Item 86
Question: why does report-worker keep crashing in shop
Ground truth: LimitRange/shop/container-defaults: its default memory limit (256Mi), created at T0, was set by admission on report-worker's pods recreated by a node drain; report-worker declares no limit and needs about 700Mi, so it is OOMKilled
Passes if: names the LimitRange's default as the source of the 256Mi limit
Partial if only: the memory limit is too low, without the LimitRange
Answer: The root cause is likely misconfiguration in the Deployment/monitoring/alertmanager. The Deployment is set to use a RollingUpdate strategy with maxSurge and maxUnavailable set to 25%, which could lead to resource contention and instability. The Deployment/monitoring/alertmanager also has a paused=false setting, which may prevent it from scaling down pods, leading to resource exhaustion.

Item 87
Question: why are new pods of cart-api and web stuck in shop
Ground truth: Deployment/mesh-system/mesh-injector: since its T0 upgrade (chart 1.21.0) the injector injects ghcr.io/acme/mesh-proxy:1.21.0-rc, a tag that does not exist, into every new pod of the namespaces its webhook selects
Passes if: names the injector or its webhook as the source of the mesh-proxy container with the bad image
Partial if only: an image cannot be pulled, without where the container comes from
Answer: The image of cart-api-9c7d8b6f5-cc8d1 cannot be pulled: the image or tag does not exist. The image of web-8c7b6d5f9-4ea43 cannot be pulled: the image or tag does not exist.

Item 88
Question: why are search-api and web crash-looping in shop
Ground truth: ConfigMap/shop/feature-flags: its features.yaml was replaced at T0 by invalid YAML (a tab on line 7); search-api and web, which mount it, reload it, exit, and fail again on every start
Passes if: names the feature-flags ConfigMap's invalid content
Partial if only: a configuration file fails to parse, without the ConfigMap
Answer: The misconfiguration in the `features.yaml` file is causing the services to crash. This is evidenced by the following events: - Event type=Warning reason=BackOff involved=Pod/search-api-6f9c8d7b4-ffafa namespace=shop count=20 message=Back-off restarting failed container search-api in pod search-api-6f9c8d7b4-ffafa - Event type=Warning reason=BackOff involved=Pod/search-api-6f9c8d7b4-019e9 namespace=shop count=20 message=Back-off restarting failed container search-api in pod search-api-6f9c8d7b4-019e9 - Event type=Warning reason=BackOff involved=Pod/web-9c8d7b6f5-4cb4b namespace=shop count=20 message=Back-off restarting failed container web in pod web-9c8d7b6f5-4cb4b - Event type=Warning reason=BackOff involved=Pod/web-9c8d7b6f5-4ea3a namespace=shop count=20 message=Back-off restarting failed container web in pod web-9c8d7b6f5-4ea3a

Item 89
Question: why do cart-api and checkout crash in shop
Ground truth: ConfigMap/shop/shop-env: its DB_HOST was changed at T0 to orders-db-v2, which does not exist; pods read it when they start, so only those recreated since T0 by a node drain fail, while older pods keep the old value
Passes if: names shop-env's DB_HOST change
Partial if only: the database host cannot be resolved, without the ConfigMap
Answer: The root cause appears to be a DNS resolution issue preventing the cart-api and checkout services from connecting to the orders-db-v2 database. This is inferred from the repeated error logs indicating "cannot connect to database" and the "dial tcp: lookup orders-db-v2.data.svc.cluster.local: no such host" error.

Item 90
Question: is something wrong with checkout-api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: No incident: no fault signal in the evidence.

Item 91
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: The root cause is a Redis cache OOM error in the pricing deployment of the `platform` namespace, which is causing pricing requests to fail and return 503 errors to the `checkout-api` in the `shop` namespace.

Item 92
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The deployment of the cart-api and checkout services is paused, which could be causing the observed CPU throttling. The deployment is set to `paused: false`, but it is likely that the deployment is paused due to an accidental setting.

Item 93
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: The Deployment/platform/pricing has drift where the spec.replicas field is set to 3 instead of the expected 5. This misconfiguration causes the deployment to scale down below the desired number of replicas, leading to the 503 errors in the checkout-api service.

Item 94
Question: why does the checkout rollout not start in shop
Ground truth: Secret/mesh-system/mesh-injector-tls: the injector's serving certificate expired at T0 and was not renewed (its issuer mesh-ca is gone), so the API server's calls to the webhook fail TLS verification, and the webhook, set to failurePolicy Fail, rejects every pod creation in shop
Passes if: names the expired certificate, or the webhook failing closed because of it
Partial if only: a webhook rejects the pods, without why
Answer: The root cause is the expired certificate used by the mesh-injector webhook, which is causing the failure to create the ReplicaSet and Job for the checkout deployment in the shop namespace.

Item 95
Question: why is the search-api rollout stuck in shop
Ground truth: LimitRange/shop/container-defaults: its maximum memory per container was lowered to 1Gi at T0, below search-api's 2Gi limit, so admission refuses every new pod of the rollout while the old pods keep running
Passes if: names the LimitRange's maximum against search-api's limit
Partial if only: admission refuses the pods, or the limit is too high, without the LimitRange
Answer: No incident: no fault signal in the evidence.

Item 96
Question: why does report-worker keep crashing in shop
Ground truth: LimitRange/shop/container-defaults: its default memory limit (256Mi), created at T0, was set by admission on report-worker's pods recreated by a node drain; report-worker declares no limit and needs about 700Mi, so it is OOMKilled
Passes if: names the LimitRange's default as the source of the 256Mi limit
Partial if only: the memory limit is too low, without the LimitRange
Answer: The container report-worker of report-worker-7d9c8b6f5-4c645 exceeds its memory limit (OOMKilled). The container report-worker of report-worker-7d9c8b6f5-4e534 exceeds its memory limit (OOMKilled).

Item 97
Question: why does orders-api keep restarting in shop
Ground truth: Deployment/shop/orders-api: the orders-api chart upgrade to 3.0.0 dropped the app=orders-api pod label, so NetworkPolicy data/allow-orders-to-db no longer selects its pods and data/default-deny-ingress drops their connections to orders-db
Passes if: names the pod label change against the policy's podSelector
Partial if only: the upgrade without the label; the policy without the label
Answer: The orders-api pods are unable to connect to the orders-db database, causing them to crash and enter a crash loop. This is likely due to a network issue or incorrect database connection configuration.

Item 98
Question: why does the checkout rollout not start in shop
Ground truth: Secret/mesh-system/mesh-injector-tls: the injector's serving certificate expired at T0 and was not renewed (its issuer mesh-ca is gone), so the API server's calls to the webhook fail TLS verification, and the webhook, set to failurePolicy Fail, rejects every pod creation in shop
Passes if: names the expired certificate, or the webhook failing closed because of it
Partial if only: a webhook rejects the pods, without why
Answer: The root cause is the expired certificate used by the mesh-injector webhook, which is causing the failure to create the ReplicaSet and Job for the checkout deployment in the shop namespace.

Item 99
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause is likely the high CPU usage in the 'search-api' container, leading to throttling. This is inferred from the Prometheus alert about elevated CPU throttling.

Item 100
Question: why can't billing-worker roll out in shop
Ground truth: Deployment/shop/catalog-api: catalog-api's live CPU request (100m) is below its applied 500m, so its HPA reads five times the utilization, scales it to its maximum of 20, and those replicas' 1Gi memory requests fill the namespace's requests.memory quota
Passes if: names Deployment/shop/catalog-api and the mechanism
Partial if only: the right object with a wrong mechanism
Answer: The billing-worker deployment is requesting 2 GiB of memory, but the available memory quota is only 32 GiB. The deployment has already used 31 GiB, causing the creation of the billing-worker-6b8c4d7f9 ReplicaSet to fail.

Item 101
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: No incident: no fault signal in the evidence.

Item 102
Question: why does orders-api keep restarting in shop
Ground truth: Deployment/shop/orders-api: the orders-api chart upgrade to 3.0.0 dropped the app=orders-api pod label, so NetworkPolicy data/allow-orders-to-db no longer selects its pods and data/default-deny-ingress drops their connections to orders-db
Passes if: names the pod label change against the policy's podSelector
Partial if only: the upgrade without the label; the policy without the label
Answer: The persistent database connection issue is causing the orders-api pods to crash and restart. This is evidenced by the repeated Back-off restarting failed container events in the logs and the Deployment's CrashLoopBackOff condition.

Item 103
Question: why does api take so long to absorb traffic peaks in shop
Ground truth: HorizontalPodAutoscaler/shop/api: the T0 release (chart 3.2.0) set behavior.scaleUp to one pod per 600 s with a 600 s stabilization window, so the HPA adds replicas too slowly and api saturates at each peak
Passes if: names the HPA's scale-up policy or rate limit
Partial if only: the HPA scales too slowly, without the policy; the release, without the policy
Answer: The `Deployment/shop/api` is paused, which is preventing it from scaling up to handle traffic peaks.

Item 104
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The cart-api deployment is paused, which halts rollouts and prevents it from being deployed or updated. This is likely due to an accidental setting or misconfiguration.

Item 105
Question: why does the checkout rollout not start in shop
Ground truth: Secret/mesh-system/mesh-injector-tls: the injector's serving certificate expired at T0 and was not renewed (its issuer mesh-ca is gone), so the API server's calls to the webhook fail TLS verification, and the webhook, set to failurePolicy Fail, rejects every pod creation in shop
Passes if: names the expired certificate, or the webhook failing closed because of it
Partial if only: a webhook rejects the pods, without why
Answer: The root cause is the expired certificate for the `mesh-injector-tls` certificate secret, which is causing the webhook `inject.mesh.acme.io` to fail, preventing the `Deployment/shop/checkout` from starting.

Item 106
Question: why does checkout-api fail to reserve stock in shop
Ground truth: Service/platform/inventory: the inventory upgrade moved the container to port 9090, while Service platform/inventory, owned by another release, still targets 8080: every connection is refused while the pods report Ready
Passes if: names the port mismatch between the Service and the inventory pods, blaming either side
Partial if only: inventory unreachable or connection refused, without the port
Answer: The Pod/shop/cart-api-5d6f8c7b9-cc8c8 and Pod/shop/cart-api-5d6f8c7b9-ce7b7 are experiencing a connection refused error when attempting to reserve stock. This indicates a network issue with the inventory service, likely due to a network configuration or service outage.

Item 107
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: No incident: no fault signal in the evidence.

Item 108
Question: why doesn't api scale up under load in shop
Ground truth: APIService/v1beta1.custom.metrics.k8s.io: the APIService still points to monitoring/prometheus-adapter, which has had no endpoints since the adapter moved to observability at T0, so the custom metrics API is unavailable and the HPA cannot read http_requests_per_second
Passes if: names the APIService, or the custom metrics API, pointing to the adapter's old location
Partial if only: the HPA cannot read its metric, or the metrics API is unavailable, without where it points
Answer: The Deployment/shop/api is set to a minReadySeconds of 0, which means the pods are considered available immediately upon creation, even if they are not fully ready. This can cause the HorizontalPodAutoscaler to not scale up the pods as expected under load.

Item 109
Question: why does orders-api keep restarting in shop
Ground truth: Deployment/shop/orders-api: the orders-api chart upgrade to 3.0.0 dropped the app=orders-api pod label, so NetworkPolicy data/allow-orders-to-db no longer selects its pods and data/default-deny-ingress drops their connections to orders-db
Passes if: names the pod label change against the policy's podSelector
Partial if only: the upgrade without the label; the policy without the label
Answer: The orders-api pods are unable to connect to the orders-db database, causing them to crash and enter a CrashLoopBackOff state. This is likely due to a network issue or incorrect database connection configuration.

Item 110
Question: why are search-api and web crash-looping in shop
Ground truth: ConfigMap/shop/feature-flags: its features.yaml was replaced at T0 by invalid YAML (a tab on line 7); search-api and web, which mount it, reload it, exit, and fail again on every start
Passes if: names the feature-flags ConfigMap's invalid content
Partial if only: a configuration file fails to parse, without the ConfigMap
Answer: The YAML parsing error in the `/etc/flags/features.yaml` file is causing the search-api and web services to crash loop. This is indicated by the repeated `Back-off restarting failed container` events in the Kubernetes events section.

Item 111
Question: why doesn't api scale up under load in shop
Ground truth: APIService/v1beta1.custom.metrics.k8s.io: the APIService still points to monitoring/prometheus-adapter, which has had no endpoints since the adapter moved to observability at T0, so the custom metrics API is unavailable and the HPA cannot read http_requests_per_second
Passes if: names the APIService, or the custom metrics API, pointing to the adapter's old location
Partial if only: the HPA cannot read its metric, or the metrics API is unavailable, without where it points
Answer: The root cause is the `Deployment/shop/api` with `spec.minReadySeconds` set to `0`, which disables the autoscaler from automatically scaling up the pods under load. Additionally, the `HorizontalPodAutoscaler/api` is unable to fetch metrics from the custom metrics API, preventing it from adjusting the number of pods based on the observed load.

Item 112
Question: why does report-worker keep crashing in shop
Ground truth: LimitRange/shop/container-defaults: its default memory limit (256Mi), created at T0, was set by admission on report-worker's pods recreated by a node drain; report-worker declares no limit and needs about 700Mi, so it is OOMKilled
Passes if: names the LimitRange's default as the source of the 256Mi limit
Partial if only: the memory limit is too low, without the LimitRange
Answer: The `report-worker` pod is consuming more CPU resources than allocated, causing CPU throttling. This excessive resource consumption leads to OOMKills due to insufficient memory.

Item 113
Question: why does checkout-api fail to reserve stock in shop
Ground truth: Service/platform/inventory: the inventory upgrade moved the container to port 9090, while Service platform/inventory, owned by another release, still targets 8080: every connection is refused while the pods report Ready
Passes if: names the port mismatch between the Service and the inventory pods, blaming either side
Partial if only: inventory unreachable or connection refused, without the port
Answer: The Pod/shop/cart-api-5d6f8c7b9-cc8c8 and Pod/shop/cart-api-5d6f8c7b9-ce7b7 pods are experiencing connection issues with the inventory service, as evidenced by the error logs.

Item 114
Question: why are new pods of cart-api and web stuck in shop
Ground truth: Deployment/mesh-system/mesh-injector: since its T0 upgrade (chart 1.21.0) the injector injects ghcr.io/acme/mesh-proxy:1.21.0-rc, a tag that does not exist, into every new pod of the namespaces its webhook selects
Passes if: names the injector or its webhook as the source of the mesh-proxy container with the bad image
Partial if only: an image cannot be pulled, without where the container comes from
Answer: The root cause is the failure to pull the required images "ghcr.io/acme/mesh-proxy:1.21.0-rc" for the cart-api and web pods. This is evidenced by the repeated "Failed to pull image" and "Back-off pulling image" events.

Item 115
Question: why doesn't api scale up under load in shop
Ground truth: APIService/v1beta1.custom.metrics.k8s.io: the APIService still points to monitoring/prometheus-adapter, which has had no endpoints since the adapter moved to observability at T0, so the custom metrics API is unavailable and the HPA cannot read http_requests_per_second
Passes if: names the APIService, or the custom metrics API, pointing to the adapter's old location
Partial if only: the HPA cannot read its metric, or the metrics API is unavailable, without where it points
Answer: No incident: no fault signal in the evidence.

Item 116
Question: is something wrong with api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The deployment for the shop/api service is paused, which halts rollouts and prevents new replicas from being created or existing ones from being updated.

Item 117
Question: why does the checkout rollout not start in shop
Ground truth: Secret/mesh-system/mesh-injector-tls: the injector's serving certificate expired at T0 and was not renewed (its issuer mesh-ca is gone), so the API server's calls to the webhook fail TLS verification, and the webhook, set to failurePolicy Fail, rejects every pod creation in shop
Passes if: names the expired certificate, or the webhook failing closed because of it
Partial if only: a webhook rejects the pods, without why
Answer: The root cause is the expired TLS certificate for the mesh-injector-tls secret, which is causing the error in the Job/report-export-29342410 and the Deployment/shop/checkout rollout not starting.

Item 118
Question: why are checkout-api's payments failing in shop
Ground truth: Namespace/shop: Namespace shop's team label changed from storefront to shop, so NetworkPolicy pay/allow-from-storefront no longer matches it and pay/default-deny-ingress drops checkout-api's calls to payments
Passes if: names the label mismatch, through the Namespace or the policy
Partial if only: a NetworkPolicy without the label mismatch
Answer: The root cause appears to be an i/o timeout when attempting to dial the payments service at `payments.pay.svc.cluster.local:8443`.

Item 119
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause is likely resource exhaustion due to high CPU usage in the 'search-api' container. This is inferred from the Prometheus alert about elevated CPU throttling.

Item 120
Question: why is the search-api rollout stuck in shop
Ground truth: LimitRange/shop/container-defaults: its maximum memory per container was lowered to 1Gi at T0, below search-api's 2Gi limit, so admission refuses every new pod of the rollout while the old pods keep running
Passes if: names the LimitRange's maximum against search-api's limit
Partial if only: admission refuses the pods, or the limit is too high, without the LimitRange
Answer: The ReplicaSet is forbidden to create because the maximum memory usage per container is 1Gi, but the limit is 2Gi.

Item 121
Question: why doesn't api scale up under load in shop
Ground truth: APIService/v1beta1.custom.metrics.k8s.io: the APIService still points to monitoring/prometheus-adapter, which has had no endpoints since the adapter moved to observability at T0, so the custom metrics API is unavailable and the HPA cannot read http_requests_per_second
Passes if: names the APIService, or the custom metrics API, pointing to the adapter's old location
Partial if only: the HPA cannot read its metric, or the metrics API is unavailable, without where it points
Answer: The root cause is the inability of the Kubernetes aggregated API v1beta1.custom.metrics.k8s.io to handle requests, which is preventing the HPA from fetching the required metric http_requests_per_second.

Item 122
Question: why does checkout-api fail to reserve stock in shop
Ground truth: Service/platform/inventory: the inventory upgrade moved the container to port 9090, while Service platform/inventory, owned by another release, still targets 8080: every connection is refused while the pods report Ready
Passes if: names the port mismatch between the Service and the inventory pods, blaming either side
Partial if only: inventory unreachable or connection refused, without the port
Answer: The `inventory` service in the `platform` namespace is not reachable by the `checkout-api` pods in the `shop` namespace, causing connection refused errors. This could be due to the service not being deployed or running, or network issues preventing communication between the two namespaces.

Item 123
Question: why does search-api not scale up in shop
Ground truth: CronJob/shop/report-export (origin: Service/data/reports-db): report-export starts a Job every 10 minutes with concurrencyPolicy Allow; each waits on reports-db, which has had no ready endpoint since T0, so none finishes and 25 Jobs fill the namespace's pods quota
Passes if: names CronJob/shop/report-export and the mechanism
Partial if only: Service/data/reports-db
Answer: The search-api deployment is unable to create new replicas because it has exceeded the shop namespace's pods quota.

Item 124
Question: why does report-worker keep crashing in shop
Ground truth: LimitRange/shop/container-defaults: its default memory limit (256Mi), created at T0, was set by admission on report-worker's pods recreated by a node drain; report-worker declares no limit and needs about 700Mi, so it is OOMKilled
Passes if: names the LimitRange's default as the source of the 256Mi limit
Partial if only: the memory limit is too low, without the LimitRange
Answer: The root cause is misconfigured restart policies in the Deployment for report-worker. The Deployment is set to restart all containers in the pod on failure, which is causing the pods to restart frequently and crash.

Item 125
Question: why is the search-api rollout stuck in shop
Ground truth: LimitRange/shop/container-defaults: its maximum memory per container was lowered to 1Gi at T0, below search-api's 2Gi limit, so admission refuses every new pod of the rollout while the old pods keep running
Passes if: names the LimitRange's maximum against search-api's limit
Partial if only: admission refuses the pods, or the limit is too high, without the LimitRange
Answer: The root cause is the exceeded maximum memory usage limit for the search-api container in the shop namespace. The limit is set to 2Gi, but the container is trying to use more than that (1Gi + unspecified additional memory usage).

Item 126
Question: why does search-api not scale up in shop
Ground truth: CronJob/shop/report-export (origin: Service/data/reports-db): report-export starts a Job every 10 minutes with concurrencyPolicy Allow; each waits on reports-db, which has had no ready endpoint since T0, so none finishes and 25 Jobs fill the namespace's pods quota
Passes if: names CronJob/shop/report-export and the mechanism
Partial if only: Service/data/reports-db
Answer: The deployment of search-api in the shop namespace is not scaling up because the namespace quota is fully used, preventing the creation of additional pods.

Item 127
Question: why does the checkout rollout not start in shop
Ground truth: Secret/mesh-system/mesh-injector-tls: the injector's serving certificate expired at T0 and was not renewed (its issuer mesh-ca is gone), so the API server's calls to the webhook fail TLS verification, and the webhook, set to failurePolicy Fail, rejects every pod creation in shop
Passes if: names the expired certificate, or the webhook failing closed because of it
Partial if only: a webhook rejects the pods, without why
Answer: The root cause is the expired TLS certificate for the mesh-injector-tls certificate secret, which is causing the error in creating the ReplicaSet and Job resources.

Item 128
Question: why does search-api not scale up in shop
Ground truth: CronJob/shop/report-export (origin: Service/data/reports-db): report-export starts a Job every 10 minutes with concurrencyPolicy Allow; each waits on reports-db, which has had no ready endpoint since T0, so none finishes and 25 Jobs fill the namespace's pods quota
Passes if: names CronJob/shop/report-export and the mechanism
Partial if only: Service/data/reports-db
Answer: The deployment of search-api in the shop namespace is constrained by a quota limit, preventing it from scaling up beyond the current limit of 40 pods.

Item 129
Question: why can't billing-worker roll out in shop
Ground truth: Deployment/shop/catalog-api: catalog-api's live CPU request (100m) is below its applied 500m, so its HPA reads five times the utilization, scales it to its maximum of 20, and those replicas' 1Gi memory requests fill the namespace's requests.memory quota
Passes if: names Deployment/shop/catalog-api and the mechanism
Partial if only: the right object with a wrong mechanism
Answer: The Deployment/shop/billing-worker is stuck in a Progressing state due to a quota exceeded error, specifically the quota for requests.memory is fully used. This is indicated by the Helm drift for the container.image field, where the observed image is ghcr.io/acme/cart-api:2.3.2, while the declared image is ghcr.io/acme/cart-api:2.3.1. This mismatch causes the deployment to fail during rolling updates.

Item 130
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause appears to be an unintentional update of the cart-api deployment image to a newer version (2.3.2) which may have introduced performance issues leading to high CPU throttling in the monitoring namespace.

Item 131
Question: is something wrong with api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The declared value for `minReadySeconds` is 0, which is less than the Kubernetes default of 30 seconds. This means that the pods are being terminated too quickly, possibly causing instability. Additionally, the deployment is paused, which is preventing new replicas from being created or existing ones from being updated.

Item 132
Question: why is the search-api rollout stuck in shop
Ground truth: LimitRange/shop/container-defaults: its maximum memory per container was lowered to 1Gi at T0, below search-api's 2Gi limit, so admission refuses every new pod of the rollout while the old pods keep running
Passes if: names the LimitRange's maximum against search-api's limit
Partial if only: admission refuses the pods, or the limit is too high, without the LimitRange
Answer: The Deployment/search-api is stuck in the Progressing state due to resource limits. Specifically, the maximum memory usage per Container is set to 1Gi, but the limit is 2Gi. This causes the Kubernetes scheduler to reject the pod creation, leading to the stuck rollout.

Item 133
Question: is something wrong with checkout-api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The Pod/shop/cart-api-5d6f8c7b9-cc8c8 and Pod/shop/cart-api-5d6f8c7b9-ce7b7 both have a readiness probe that fails, as evidenced by the Event message "Readiness probe failed: Get "http://10.42.1.39:9090/ready": dial tcp 10.42.1.39:9090: connect: connection refused". This indicates that the service at the endpoint 10.42.1.39:9090 is not reachable.

Item 134
Question: why are new pods of cart-api and web stuck in shop
Ground truth: Deployment/mesh-system/mesh-injector: since its T0 upgrade (chart 1.21.0) the injector injects ghcr.io/acme/mesh-proxy:1.21.0-rc, a tag that does not exist, into every new pod of the namespaces its webhook selects
Passes if: names the injector or its webhook as the source of the mesh-proxy container with the bad image
Partial if only: an image cannot be pulled, without where the container comes from
Answer: The root cause is the failure to pull the required images "ghcr.io/acme/mesh-proxy:1.21.0-rc" and "ghcr.io/acme/mesh-proxy:1.21.0-rc" for the pods cart-api and web, respectively. This is indicated by the events and alerts related to image pull failures.

Item 135
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The deployment strategy for the cart-api and checkout services is set to RollingUpdate, which may not be optimal for a service experiencing CPU throttling. This could be due to the high maxSurge and maxUnavailable settings, which might be causing the pods to be restarted too frequently and leading to CPU throttling.

Item 136
Question: is something wrong with api in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: No incident: no fault signal in the evidence.

Item 137
Question: why does api take so long to absorb traffic peaks in shop
Ground truth: HorizontalPodAutoscaler/shop/api: the T0 release (chart 3.2.0) set behavior.scaleUp to one pod per 600 s with a 600 s stabilization window, so the HPA adds replicas too slowly and api saturates at each peak
Passes if: names the HPA's scale-up policy or rate limit
Partial if only: the HPA scales too slowly, without the policy; the release, without the policy
Answer: The root cause is the insufficient resources allocated to the `api` deployment, which is causing it to struggle during traffic peaks. This is supported by the `PodDisruptionBudget` and the `Pod` events indicating high resource usage and latency.

Item 138
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The root cause is likely that the deployments for "shop/cart-api" and "shop/checkout" are paused, which is contrary to the expected operational state. This is indicated by the `paused=true` field in the deployment objects.

Item 139
Question: why does the checkout rollout not start in shop
Ground truth: Secret/mesh-system/mesh-injector-tls: the injector's serving certificate expired at T0 and was not renewed (its issuer mesh-ca is gone), so the API server's calls to the webhook fail TLS verification, and the webhook, set to failurePolicy Fail, rejects every pod creation in shop
Passes if: names the expired certificate, or the webhook failing closed because of it
Partial if only: a webhook rejects the pods, without why
Answer: No incident: no fault signal in the evidence.

Item 140
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: The Deployment/platform/pricing has drifted from its declared values, with the `spec.replicas` field set to 5 instead of the declared 3. This misconfiguration causes the deployment to scale up to 5 replicas, leading to resource contention and errors when the service is queried.

Item 141
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: No incident: no fault signal in the evidence.

Item 142
Question: why are new pods of cart-api and web stuck in shop
Ground truth: Deployment/mesh-system/mesh-injector: since its T0 upgrade (chart 1.21.0) the injector injects ghcr.io/acme/mesh-proxy:1.21.0-rc, a tag that does not exist, into every new pod of the namespaces its webhook selects
Passes if: names the injector or its webhook as the source of the mesh-proxy container with the bad image
Partial if only: an image cannot be pulled, without where the container comes from
Answer: The root cause is the failure to pull the required images `ghcr.io/acme/mesh-proxy:1.21.0-rc` for the `cart-api` and `web` pods. This is evidenced by the repeated `Failed to pull image` events in the Kubernetes events section.

Item 143
Question: why do cart-api and checkout crash in shop
Ground truth: ConfigMap/shop/shop-env: its DB_HOST was changed at T0 to orders-db-v2, which does not exist; pods read it when they start, so only those recreated since T0 by a node drain fail, while older pods keep the old value
Passes if: names shop-env's DB_HOST change
Partial if only: the database host cannot be resolved, without the ConfigMap
Answer: The database connectivity issue is the root cause, indicated by the repeated error messages in the logs and the Back-off restarting failed container events.

Item 144
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The declared maxSurge and maxUnavailable values for the cart-api deployment are set to 25%, which may cause unexpected downtime during updates. This is a deviation from the default Kubernetes values and may not be intentional.

Item 145
Question: is something wrong in shop
Ground truth: No incident.
Passes if: the answer says there is no incident and names no fault
Partial if only: never
Answer: The cart-api deployment is paused, which halts rollouts and prevents it from being deployed or updated. This is likely due to an accidental setting or misconfiguration.

Item 146
Question: why does report-worker keep crashing in shop
Ground truth: LimitRange/shop/container-defaults: its default memory limit (256Mi), created at T0, was set by admission on report-worker's pods recreated by a node drain; report-worker declares no limit and needs about 700Mi, so it is OOMKilled
Passes if: names the LimitRange's default as the source of the 256Mi limit
Partial if only: the memory limit is too low, without the LimitRange
Answer: The root cause is likely a misconfiguration in the Deployment for the report-worker service. The Deployment's `replicas` setting is set to `1`, which means it will only have one instance running at any time. Given the observed `Back-off restarting failed container` events, it suggests that the deployment is failing to scale up to meet demand, leading to resource contention and crashes.

Item 147
Question: why does the checkout rollout not start in shop
Ground truth: Secret/mesh-system/mesh-injector-tls: the injector's serving certificate expired at T0 and was not renewed (its issuer mesh-ca is gone), so the API server's calls to the webhook fail TLS verification, and the webhook, set to failurePolicy Fail, rejects every pod creation in shop
Passes if: names the expired certificate, or the webhook failing closed because of it
Partial if only: a webhook rejects the pods, without why
Answer: The root cause is the expired certificate used by the mesh-injector webhook, which is causing the failure to create the ReplicaSet and Job for the checkout deployment in the shop namespace.

