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

There are 5 items.

---

Item 24
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: The Deployment/platform/pricing has drifted, with the spec.replicas field set to 3 instead of the expected 5. This misconfiguration causes the deployment to scale down below the desired number of replicas, leading to the observed errors.

Item 51
Question: why does api take so long to absorb traffic peaks in shop
Ground truth: HorizontalPodAutoscaler/shop/api: the T0 release (chart 3.2.0) set behavior.scaleUp to one pod per 600 s with a 600 s stabilization window, so the HPA adds replicas too slowly and api saturates at each peak
Passes if: names the HPA's scale-up policy or rate limit
Partial if only: the HPA scales too slowly, without the policy; the release, without the policy
Answer: The `PodDisruptionBudget` and `HorizontalPodAutoscaler` configurations for the `api` deployment in the `shop` namespace are set to allow for more pod disruptions and scaling down than necessary, resulting in insufficient resources during traffic peaks.

Item 83
Question: why is the search-api rollout stuck in shop
Ground truth: LimitRange/shop/container-defaults: its maximum memory per container was lowered to 1Gi at T0, below search-api's 2Gi limit, so admission refuses every new pod of the rollout while the old pods keep running
Passes if: names the LimitRange's maximum against search-api's limit
Partial if only: admission refuses the pods, or the limit is too high, without the LimitRange
Answer: The Deployment/search-api is stuck in a rollout due to resource limits exceeding the specified maximum memory usage. The Deployment/monitoring/alertmanager resource has a maximum memory limit of 2Gi, but the Deployment/search-api has a maximum memory limit of 1Gi, causing the error.

Item 93
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: The Deployment/platform/pricing has drift where the spec.replicas field is set to 3 instead of the expected 5. This misconfiguration causes the deployment to scale down below the desired number of replicas, leading to the 503 errors in the checkout-api service.

Item 140
Question: why does checkout-api return errors on pricing in shop
Ground truth: Deployment/cache/redis (origin: Deployment/platform/pricing): Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, which since its T0 release caches every price without a TTL, fails on them and returns 503 to checkout-api
Passes if: names Redis's memory limit or eviction policy as what refuses the writes
Partial if only: pricing as failing, without Redis's limit
Answer: The Deployment/platform/pricing has drifted from its declared values, with the `spec.replicas` field set to 5 instead of the declared 3. This misconfiguration causes the deployment to scale up to 5 replicas, leading to resource contention and errors when the service is queried.
