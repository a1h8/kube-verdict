# ──────────────────────────────────────────────────────────────────────────────
# AUTO-GENERATED — do not edit by hand.
# Re-generate with:  python tools/gen_entities.py
# Source of truth:   ontology/gen_config.yaml
#                    kubernetes.client.models (openapi_types)
# ──────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
from dataclasses import dataclass, field

from ontology.entities import K8sEntity, ResourceKind



# ── ResourceKind entries required in ontology/entities.py ──────────────────
# Add these to the ResourceKind enum if not already present:
#   LIMITRANGE = "LimitRange"
#   NETWORKPOLICY = "NetworkPolicy"
#   HORIZONTALPODAUTOSCALER = "HorizontalPodAutoscaler"
#   JOB = "Job"
#   CRONJOB = "CronJob"
#   STORAGECLASS = "StorageClass"


@dataclass
class LimitRange(K8sEntity):
    """Per-namespace min/max constraints on container resource requests and limits."""
    limits: list[dict] = field(default_factory=list)  # Each item sets min/max/default/defaultRequest for a type (Container/Pod/PVC).

    def __post_init__(self):
        self.kind = ResourceKind.LIMITRANGE

    def to_text(self) -> str:
        parts = [f'kind=LimitRange name={self.name}']
        if self.namespace:
            parts.append(f'namespace={self.namespace}')
        if self.limits:
            parts.append(f'limits={self.limits}')
        return ' '.join(parts)

@dataclass
class NetworkPolicy(K8sEntity):
    """Namespace-scoped firewall rules controlling pod ingress and egress."""
    pod_selector: str = ""  # Pods this policy applies to (empty = all pods in namespace).
    policy_types: list[str] = field(default_factory=list)  # Directions enforced: Ingress, Egress, or both.
    ingress_rules: list[dict] = field(default_factory=list)  # Allowed ingress traffic sources (ports + from selectors).
    egress_rules: list[dict] = field(default_factory=list)  # Allowed egress traffic destinations (ports + to selectors).

    def __post_init__(self):
        self.kind = ResourceKind.NETWORKPOLICY

    def to_text(self) -> str:
        parts = [f'kind=NetworkPolicy name={self.name}']
        if self.namespace:
            parts.append(f'namespace={self.namespace}')
        if self.pod_selector:
            parts.append(f'pod_selector={self.pod_selector}')
        if self.policy_types:
            parts.append(f'policy_types={self.policy_types}')
        if self.ingress_rules:
            parts.append(f'ingress_rules={self.ingress_rules}')
        if self.egress_rules:
            parts.append(f'egress_rules={self.egress_rules}')
        return ' '.join(parts)

@dataclass
class HorizontalPodAutoscaler(K8sEntity):
    """Autoscaler that scales a workload based on CPU/memory/custom metrics."""
    target_name: str = ""  # Workload being scaled (Deployment, StatefulSet, etc.).
    min_replicas: int = 0  # Minimum number of replicas.
    max_replicas: int = 0  # Maximum number of replicas.
    target_cpu_pct: int = 0  # Target average CPU utilisation across pods (autoscaling/v1 style).
    current_replicas: int = 0  # Current observed replica count.
    desired_replicas: int = 0  # Desired replica count computed by the HPA controller.

    def __post_init__(self):
        self.kind = ResourceKind.HORIZONTALPODAUTOSCALER

    def to_text(self) -> str:
        parts = [f'kind=HorizontalPodAutoscaler name={self.name}']
        if self.namespace:
            parts.append(f'namespace={self.namespace}')
        if self.target_name:
            parts.append(f'target_name={self.target_name}')
        if self.min_replicas:
            parts.append(f'min_replicas={self.min_replicas}')
        if self.max_replicas:
            parts.append(f'max_replicas={self.max_replicas}')
        if self.target_cpu_pct:
            parts.append(f'target_cpu_pct={self.target_cpu_pct}')
        if self.current_replicas:
            parts.append(f'current_replicas={self.current_replicas}')
        if self.desired_replicas:
            parts.append(f'desired_replicas={self.desired_replicas}')
        return ' '.join(parts)

@dataclass
class Job(K8sEntity):
    """A batch Job that runs Pods to completion."""
    completions: int = 0  # Desired number of successfully finished pods.
    parallelism: int = 0  # Maximum pods running in parallel.
    backoff_limit: int = 0  # Number of retries before marking the Job as failed.
    succeeded: int = 0  # Number of pods that have successfully completed.
    failed: int = 0  # Number of pods that have failed.
    active: int = 0  # Number of currently running pods.

    def __post_init__(self):
        self.kind = ResourceKind.JOB

    def to_text(self) -> str:
        parts = [f'kind=Job name={self.name}']
        if self.namespace:
            parts.append(f'namespace={self.namespace}')
        if self.completions:
            parts.append(f'completions={self.completions}')
        if self.parallelism:
            parts.append(f'parallelism={self.parallelism}')
        if self.backoff_limit:
            parts.append(f'backoff_limit={self.backoff_limit}')
        if self.succeeded:
            parts.append(f'succeeded={self.succeeded}')
        if self.failed:
            parts.append(f'failed={self.failed}')
        if self.active:
            parts.append(f'active={self.active}')
        return ' '.join(parts)

@dataclass
class CronJob(K8sEntity):
    """A CronJob creates Jobs on a repeating schedule."""
    schedule: str = ""  # Cron expression for the schedule (e.g. '*/5 * * * *').
    concurrency_policy: str = ""  # How to handle concurrent runs: Allow, Forbid, or Replace.
    suspend: bool = False  # If true, no new Jobs will be created.
    failed_jobs_history_limit: int = 0  # Number of failed Jobs to retain.
    last_schedule_time: str = ""  # Last time the CronJob was scheduled.

    def __post_init__(self):
        self.kind = ResourceKind.CRONJOB

    def to_text(self) -> str:
        parts = [f'kind=CronJob name={self.name}']
        if self.namespace:
            parts.append(f'namespace={self.namespace}')
        if self.schedule:
            parts.append(f'schedule={self.schedule}')
        if self.concurrency_policy:
            parts.append(f'concurrency_policy={self.concurrency_policy}')
        if self.suspend:
            parts.append(f'suspend={self.suspend}')
        if self.failed_jobs_history_limit:
            parts.append(f'failed_jobs_history_limit={self.failed_jobs_history_limit}')
        if self.last_schedule_time:
            parts.append(f'last_schedule_time={self.last_schedule_time}')
        return ' '.join(parts)

@dataclass
class StorageClass(K8sEntity):
    """Defines a class of storage offered by the cluster (provisioner, reclaim policy, etc.)."""
    provisioner: str = ""  # Provisioner that handles this StorageClass (e.g. 'rancher.io/local-path').
    reclaim_policy: str = ""  # What happens to the PV when the PVC is deleted: Retain or Delete.
    volume_binding_mode: str = ""  # When to provision and bind the volume: Immediate or WaitForFirstConsumer.
    allow_volume_expansion: bool = False  # Whether PVCs using this class can be expanded after creation.

    def __post_init__(self):
        self.kind = ResourceKind.STORAGECLASS

    def to_text(self) -> str:
        parts = [f'kind=StorageClass name={self.name}']
        if self.namespace:
            parts.append(f'namespace={self.namespace}')
        if self.provisioner:
            parts.append(f'provisioner={self.provisioner}')
        if self.reclaim_policy:
            parts.append(f'reclaim_policy={self.reclaim_policy}')
        if self.volume_binding_mode:
            parts.append(f'volume_binding_mode={self.volume_binding_mode}')
        if self.allow_volume_expansion:
            parts.append(f'allow_volume_expansion={self.allow_volume_expansion}')
        return ' '.join(parts)
