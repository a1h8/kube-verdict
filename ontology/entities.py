from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ResourceKind(str, Enum):
    NAMESPACE = "Namespace"
    NODE = "Node"
    POD = "Pod"
    DEPLOYMENT = "Deployment"
    STATEFULSET = "StatefulSet"
    DAEMONSET = "DaemonSet"
    REPLICASET = "ReplicaSet"
    SERVICE = "Service"
    INGRESS = "Ingress"
    CONFIGMAP = "ConfigMap"
    SECRET = "Secret"
    PERSISTENT_VOLUME = "PersistentVolume"
    PERSISTENT_VOLUME_CLAIM = "PersistentVolumeClaim"
    SERVICE_ACCOUNT = "ServiceAccount"
    HELM_RELEASE = "HelmRelease"
    HELM_CHART = "HelmChart"
    EVENT = "Event"


@dataclass
class K8sEntity:
    uid: str
    name: str
    # Subclasses override kind in __post_init__; sentinel default avoids
    # requiring callers to pass it when constructing typed subclasses.
    kind: ResourceKind = field(default=ResourceKind.NAMESPACE)
    namespace: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    created_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def fqn(self) -> str:
        """Fully-qualified name: kind/namespace/name."""
        if self.namespace:
            return f"{self.kind.value}/{self.namespace}/{self.name}"
        return f"{self.kind.value}/{self.name}"

    def to_text(self) -> str:
        """Human-readable representation used for embedding."""
        kind_str = self.kind.value if hasattr(self.kind, "value") else str(self.kind)
        parts = [f"kind={kind_str}", f"name={self.name}"]
        if self.namespace:
            parts.append(f"namespace={self.namespace}")
        if self.labels:
            label_str = " ".join(f"{k}={v}" for k, v in self.labels.items())
            parts.append(f"labels=[{label_str}]")
        # Append drift annotations (Helm declared vs observed)
        drift_parts = [v for k, v in self.annotations.items() if k.startswith("drift.")]
        if drift_parts:
            parts.append("DRIFT=[" + " | ".join(drift_parts) + "]")
        # Append PatchTST signal anomalies
        signal_parts = [v for k, v in self.annotations.items() if k.startswith("signal.")]
        if signal_parts:
            parts.append("SIGNAL=[" + " | ".join(signal_parts) + "]")
        return " ".join(parts)


@dataclass
class Namespace(K8sEntity):
    phase: str = "Active"

    def __post_init__(self):
        self.kind = ResourceKind.NAMESPACE
        self.namespace = None


@dataclass
class Node(K8sEntity):
    ready: bool = True
    allocatable_cpu: str = ""
    allocatable_memory: str = ""
    conditions: list[dict] = field(default_factory=list)
    taints: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.kind = ResourceKind.NODE

    def to_text(self) -> str:
        base = super().to_text()
        return f"{base} ready={self.ready} cpu={self.allocatable_cpu} memory={self.allocatable_memory}"


@dataclass
class Pod(K8sEntity):
    phase: str = "Unknown"
    node_name: str = ""
    restart_count: int = 0
    container_statuses: list[dict] = field(default_factory=list)
    conditions: list[dict] = field(default_factory=list)
    owner_ref_kind: str = ""
    owner_ref_name: str = ""

    def __post_init__(self):
        self.kind = ResourceKind.POD

    @property
    def is_unhealthy(self) -> bool:
        return self.phase not in ("Running", "Succeeded")

    def to_text(self) -> str:
        base = super().to_text()
        return (
            f"{base} phase={self.phase} node={self.node_name} "
            f"restarts={self.restart_count} owner={self.owner_ref_kind}/{self.owner_ref_name}"
        )


@dataclass
class Deployment(K8sEntity):
    replicas: int = 0
    ready_replicas: int = 0
    available_replicas: int = 0
    strategy: str = "RollingUpdate"
    selector: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.kind = ResourceKind.DEPLOYMENT

    @property
    def is_degraded(self) -> bool:
        return self.ready_replicas < self.replicas

    def to_text(self) -> str:
        base = super().to_text()
        return f"{base} replicas={self.replicas} ready={self.ready_replicas} available={self.available_replicas}"


@dataclass
class StatefulSet(K8sEntity):
    replicas: int = 0
    ready_replicas: int = 0
    selector: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.kind = ResourceKind.STATEFULSET

    def to_text(self) -> str:
        base = super().to_text()
        return f"{base} replicas={self.replicas} ready={self.ready_replicas}"


@dataclass
class DaemonSet(K8sEntity):
    desired: int = 0
    ready: int = 0
    selector: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.kind = ResourceKind.DAEMONSET

    def to_text(self) -> str:
        base = super().to_text()
        return f"{base} desired={self.desired} ready={self.ready}"


@dataclass
class ReplicaSet(K8sEntity):
    replicas: int = 0
    ready_replicas: int = 0
    owner_ref_name: str = ""
    selector: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.kind = ResourceKind.REPLICASET


@dataclass
class Service(K8sEntity):
    service_type: str = "ClusterIP"
    cluster_ip: str = ""
    ports: list[dict] = field(default_factory=list)
    selector: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.kind = ResourceKind.SERVICE

    def to_text(self) -> str:
        base = super().to_text()
        port_str = " ".join(f"{p.get('port')}/{p.get('protocol','TCP')}" for p in self.ports)
        return f"{base} type={self.service_type} ports=[{port_str}]"


@dataclass
class Ingress(K8sEntity):
    rules: list[dict] = field(default_factory=list)
    tls: list[dict] = field(default_factory=list)
    ingress_class: str = ""

    def __post_init__(self):
        self.kind = ResourceKind.INGRESS


@dataclass
class ConfigMap(K8sEntity):
    data_keys: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.kind = ResourceKind.CONFIGMAP

    def to_text(self) -> str:
        base = super().to_text()
        return f"{base} keys=[{' '.join(self.data_keys)}]"


@dataclass
class Secret(K8sEntity):
    secret_type: str = "Opaque"
    data_keys: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.kind = ResourceKind.SECRET

    def to_text(self) -> str:
        base = super().to_text()
        # Never expose values — keys only
        return f"{base} type={self.secret_type} keys=[{' '.join(self.data_keys)}]"


@dataclass
class PersistentVolume(K8sEntity):
    capacity: str = ""
    access_modes: list[str] = field(default_factory=list)
    reclaim_policy: str = "Retain"
    status_phase: str = "Available"
    storage_class: str = ""

    def __post_init__(self):
        self.kind = ResourceKind.PERSISTENT_VOLUME
        self.namespace = None

    def to_text(self) -> str:
        base = super().to_text()
        return f"{base} capacity={self.capacity} phase={self.status_phase} storageClass={self.storage_class}"


@dataclass
class PersistentVolumeClaim(K8sEntity):
    requested_storage: str = ""
    access_modes: list[str] = field(default_factory=list)
    status_phase: str = "Pending"
    storage_class: str = ""
    volume_name: str = ""

    def __post_init__(self):
        self.kind = ResourceKind.PERSISTENT_VOLUME_CLAIM

    def to_text(self) -> str:
        base = super().to_text()
        return f"{base} storage={self.requested_storage} phase={self.status_phase} volume={self.volume_name}"


@dataclass
class ServiceAccount(K8sEntity):
    secrets: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.kind = ResourceKind.SERVICE_ACCOUNT


@dataclass
class HelmRelease(K8sEntity):
    chart: str = ""
    chart_version: str = ""
    app_version: str = ""
    status: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    # Helmfile-specific fields
    source: str = "helm"          # "helm" | "helmfile"
    environment: str = ""         # helmfile environment name
    value_files: list[str] = field(default_factory=list)   # paths of value files used
    needs: list[str] = field(default_factory=list)         # helmfile needs: references

    def __post_init__(self):
        self.kind = ResourceKind.HELM_RELEASE

    def to_text(self) -> str:
        base = super().to_text()
        parts = [
            f"{base} chart={self.chart} chartVersion={self.chart_version}",
            f"appVersion={self.app_version} status={self.status} source={self.source}",
        ]
        if self.environment:
            parts.append(f"environment={self.environment}")
        if self.value_files:
            parts.append(f"valueFiles=[{' '.join(self.value_files)}]")
        if self.values:
            # Flatten top-level values into searchable tokens
            flat = " ".join(f"{k}={v}" for k, v in self.values.items()
                            if not isinstance(v, (dict, list)))
            if flat:
                parts.append(f"values=[{flat}]")
        return " ".join(parts)


@dataclass
class K8sEvent(K8sEntity):
    reason: str = ""
    message: str = ""
    event_type: str = "Normal"  # Normal | Warning
    involved_kind: str = ""
    involved_name: str = ""
    count: int = 1
    first_time: datetime | None = None
    last_time: datetime | None = None

    def __post_init__(self):
        self.kind = ResourceKind.EVENT

    @property
    def is_warning(self) -> bool:
        return self.event_type == "Warning"

    def to_text(self) -> str:
        return (
            f"kind=Event type={self.event_type} reason={self.reason} "
            f"involved={self.involved_kind}/{self.involved_name} "
            f"namespace={self.namespace} count={self.count} message={self.message}"
        )


@dataclass
class ChartDependency:
    """A sub-chart declared in Chart.yaml dependencies."""
    name: str
    version: str
    repository: str = ""
    alias: str = ""            # alias used as values key prefix
    condition: str = ""        # e.g. "postgresql.enabled"
    tags: list[str] = field(default_factory=list)

    @property
    def values_key(self) -> str:
        """The key under which this sub-chart's values live in the parent values."""
        return self.alias or self.name


@dataclass
class HelmChart(K8sEntity):
    """
    Represents a Helm chart definition (static, not a deployed instance).
    uid = "chart-{name}-{version}"
    """
    chart_version: str = ""
    chart_api_version: str = "v2"   # v1 | v2
    description: str = ""
    chart_type: str = "application"  # application | library
    is_umbrella: bool = False
    dependencies: list[ChartDependency] = field(default_factory=list)
    default_values: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""

    def __post_init__(self):
        self.kind = ResourceKind.HELM_CHART
        self.namespace = None

    def to_text(self) -> str:
        dep_names = " ".join(d.name for d in self.dependencies)
        flat_vals = " ".join(
            f"{k}={v}" for k, v in self.default_values.items()
            if not isinstance(v, (dict, list))
        )
        parts = [
            f"kind=HelmChart name={self.name} version={self.chart_version}",
            f"type={self.chart_type} umbrella={self.is_umbrella}",
        ]
        if self.description:
            parts.append(f"description={self.description}")
        if dep_names:
            parts.append(f"dependencies=[{dep_names}]")
        if flat_vals:
            parts.append(f"defaultValues=[{flat_vals}]")
        return " ".join(parts)


@dataclass
class DriftItem:
    """
    Records a single discrepancy between Helm-declared state and K8s-observed state.
    Attached to entities as structured metadata (not a graph node).
    """
    field_path: str            # dot-notation: "spec.replicas", "image.tag", etc.
    declared: Any              # what Helm says
    observed: Any              # what K8s API shows
    severity: str              # "info" | "warning" | "critical"
    source: str = ""           # which collector detected it

    def to_text(self) -> str:
        return (
            f"drift field={self.field_path} "
            f"declared={self.declared} observed={self.observed} "
            f"severity={self.severity}"
        )
