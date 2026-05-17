from __future__ import annotations
import time

_DEMO_RESPONSE = """\
1. Summary
Three application root causes identified in kubewhisperer-demo + one infrastructure constraint. \
db-primary was manually scaled to 0 (Helm drift: replicas 1→0), cascading into payment-api \
CrashLoopBackOff; analytics-worker is OOMKilled due to a deployed memory limit of 50Mi vs \
256Mi declared in the Helm chart (GitOps drift); notification-svc cannot pull its image because \
:latest was removed from the registry while the chart pins v3.2.1. ml-inference remains Pending \
due to insufficient nvidia.com/gpu capacity — this requires infrastructure provisioning, not \
application remediation.

2. Affected resources
- deployment/kubewhisperer-demo/db-primary — scaled to 0, Helm drift (replicas 1→0)
- deployment/kubewhisperer-demo/payment-api — CrashLoopBackOff (47 restarts), cascade from db-primary
- pod/kubewhisperer-demo/analytics-worker — OOMKilled (12 restarts), memory limit drift
- deployment/kubewhisperer-demo/notification-svc — ImagePullBackOff, spec.template.spec.containers[0].image drift
- pod/kubewhisperer-demo/ml-inference-0 — Pending, no GPU node

3. Root cause
db-primary was scaled to 0 manually, diverging from the Helm-declared replicas=1. \
The Service has 0 ready endpoints, so every payment-api connection attempt returns \
"connection refused" (TCP 10.96.5.22:5432). analytics-worker's deployed memory limit \
is 50Mi while the Helm chart declares 256Mi — an undeployed GitOps drift causing \
repeated OOM kills. notification-svc is running :latest which was purged from the \
registry; the chart pins v3.2.1 which is still available. ml-inference cannot be \
scheduled because no node advertises nvidia.com/gpu capacity.

4. Causal chain
- db-primary manually scaled to 0 → Service has 0 ready endpoints
- payment-api TCP connect to 10.96.5.22:5432 refused → CrashLoopBackOff (47 restarts)
- analytics-worker memory limit 50Mi < actual usage 200Mi → OOMKilled (12 restarts)
- notification-svc image :latest removed from registry → ImagePullBackOff
- ml-inference nvidia.com/gpu: 1 requested → 0/1 nodes available → Pending

5. Remediation
Immediate mitigation (kubectl):
- kubectl -n kubewhisperer-demo scale deployment/db-primary --replicas=1
- kubectl -n kubewhisperer-demo rollout status deployment/db-primary
- kubectl -n kubewhisperer-demo patch deployment analytics-worker --type=strategic -p '{"spec":{"template":{"spec":{"containers":[{"name":"analytics-worker","resources":{"limits":{"memory":"256Mi"}}}]}}}}'
- kubectl -n kubewhisperer-demo set image deployment/notification-svc notification-svc=myregistry.io/notification:v3.2.1

GitOps remediation (Helm — required to prevent regression):
- helm upgrade kubewhisperer-demo ./chart --set dbPrimary.replicas=1 --dry-run
- helm upgrade kubewhisperer-demo ./chart --set dbPrimary.replicas=1
- helm upgrade kubewhisperer-demo ./chart --set analyticsWorker.resources.limits.memory=256Mi
- helm upgrade kubewhisperer-demo ./chart --set notificationSvc.image.tag=v3.2.1

Note: ml-inference remains Pending — GPU node provisioning required (infra action, out of scope).

6. Confidence
HIGH — db-primary scale drift confirmed by anchor.spec.replicas (1 vs 0). \
Memory and image tag drift confirmed by anchor violations. \
ml-inference GPU constraint is a known infra limitation, not an application bug.
"""


class DemoClient:
    """Pre-baked LLM client for demo recordings. No network calls, instant response."""

    model = "demo"

    def is_available(self) -> bool:
        return True

    def model_is_pulled(self) -> bool:
        return True

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        time.sleep(1.2)  # realistic LLM latency feel
        return _DEMO_RESPONSE

    def chat(self, messages: list[dict], temperature: float = 0.1) -> str:
        time.sleep(1.2)
        return _DEMO_RESPONSE
