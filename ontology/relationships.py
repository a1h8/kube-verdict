from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class RelationshipType(str, Enum):
    # Ownership / hierarchy
    OWNS = "OWNS"                     # Deployment → ReplicaSet → Pod
    RUNS_ON = "RUNS_ON"               # Pod → Node
    IN_NAMESPACE = "IN_NAMESPACE"     # any namespaced resource → Namespace

    # Networking
    EXPOSES = "EXPOSES"               # Service → Pod (via label selector match)
    ROUTES_TO = "ROUTES_TO"          # Ingress → Service

    # Configuration
    MOUNTS_CONFIGMAP = "MOUNTS_CONFIGMAP"   # Pod → ConfigMap
    MOUNTS_SECRET = "MOUNTS_SECRET"         # Pod → Secret
    USES_PVC = "USES_PVC"                   # Pod → PersistentVolumeClaim
    BINDS_PV = "BINDS_PV"                   # PersistentVolumeClaim → PersistentVolume
    USES_SERVICE_ACCOUNT = "USES_SERVICE_ACCOUNT"  # Pod → ServiceAccount

    # Helm
    MANAGED_BY_HELM = "MANAGED_BY_HELM"    # any resource → HelmRelease
    DEPENDS_ON = "DEPENDS_ON"             # HelmRelease → HelmRelease (helmfile needs:)
    OVERRIDES_VALUES = "OVERRIDES_VALUES"  # HelmRelease env-values → HelmRelease base
    DEPLOYED_FROM = "DEPLOYED_FROM"        # HelmRelease → HelmChart
    CHART_DEPENDENCY = "CHART_DEPENDENCY"  # HelmChart → HelmChart (umbrella sub-chart)
    HOSTED_BY = "HOSTED_BY"               # HelmChart → HelmRepository
    DEPLOYS_IN = "DEPLOYS_IN"             # HelmRelease → HelmfileEnvironment
    DRIFTS_FROM = "DRIFTS_FROM"           # K8s resource → HelmRelease (observed ≠ declared)

    # Events
    HAS_EVENT = "HAS_EVENT"           # any resource → K8sEvent

    # Observability
    HAS_ALERT = "HAS_ALERT"           # K8s entity → PrometheusAlert


@dataclass(frozen=True)
class Edge:
    source_uid: str
    target_uid: str
    rel_type: RelationshipType
    metadata: dict = None

    def __post_init__(self):
        # frozen dataclass: use object.__setattr__ to set mutable default
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})
