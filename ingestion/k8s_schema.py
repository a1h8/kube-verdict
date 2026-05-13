"""
K8s API schema — embedded defaults, valid values, and descriptions
for common Kubernetes resource fields.

Two modes:
  Embedded schema  — static (always available, no network)
  K8sApiSchema     — optionally enriches from a live API server's OpenAPI
                     endpoint; falls back to the embedded schema on failure.

Used by AnchorEngine to annotate graph entities with ground-truth knowledge:
  - What the K8s default is for a field
  - What values are valid (enums)
  - What happens when the field is misconfigured
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Embedded field schema
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FieldMeta:
    description: str = ""
    k8s_default: str = ""
    valid_values: tuple[str, ...] = ()
    severity_on_drift: str = "warning"

    def to_text(self) -> str:
        parts: list[str] = []
        if self.k8s_default:
            parts.append(f"k8s_default={self.k8s_default!r}")
        if self.valid_values:
            parts.append(f"valid={'|'.join(self.valid_values)}")
        if self.description:
            parts.append(self.description[:120])
        return " | ".join(parts)


# Field paths use '*' as a wildcard for container names.
# Priority: manifest-derived anchors > helm-derived > k8s_defaults.

_SCHEMA: dict[str, dict[str, FieldMeta]] = {

    "Pod": {
        "spec.restartPolicy": FieldMeta(
            description="Restart policy for all containers. Never = no restart on crash.",
            k8s_default="Always",
            valid_values=("Always", "OnFailure", "Never"),
            severity_on_drift="critical",
        ),
        "spec.terminationGracePeriodSeconds": FieldMeta(
            description="Seconds before SIGKILL is sent after SIGTERM. 0 = immediate kill.",
            k8s_default="30",
            severity_on_drift="warning",
        ),
        "spec.dnsPolicy": FieldMeta(
            description="DNS policy. Default=ClusterFirst. None=custom resolv.conf only.",
            k8s_default="ClusterFirst",
            valid_values=("ClusterFirst", "ClusterFirstWithHostNet", "Default", "None"),
            severity_on_drift="info",
        ),
        "spec.serviceAccountName": FieldMeta(
            description="ServiceAccount to run as. 'default' if unset.",
            k8s_default="default",
            severity_on_drift="info",
        ),
        "container.*.imagePullPolicy": FieldMeta(
            description="IfNotPresent skips pull if image is cached. Always re-pulls every start.",
            k8s_default="IfNotPresent",
            valid_values=("Always", "IfNotPresent", "Never"),
            severity_on_drift="warning",
        ),
        "container.*.resources.limits.memory": FieldMeta(
            description="Container memory hard limit. Exceeding it causes OOMKilled (exit 137).",
            severity_on_drift="critical",
        ),
        "container.*.resources.limits.cpu": FieldMeta(
            description="Container CPU hard limit. Exceeding it causes throttling, not eviction.",
            severity_on_drift="warning",
        ),
        "container.*.resources.requests.memory": FieldMeta(
            description="Memory reserved for scheduling. Node must have this free.",
            severity_on_drift="warning",
        ),
        "container.*.resources.requests.cpu": FieldMeta(
            description="CPU reserved for scheduling.",
            severity_on_drift="info",
        ),
        "container.*.livenessProbe": FieldMeta(
            description="No liveness probe means a hung process is never restarted.",
            severity_on_drift="warning",
        ),
        "container.*.readinessProbe": FieldMeta(
            description="No readiness probe means traffic arrives before the app is ready.",
            severity_on_drift="warning",
        ),
        "container.*.terminationMessagePolicy": FieldMeta(
            description="FallbackToLogsOnError captures stderr when termination message is empty.",
            k8s_default="File",
            valid_values=("File", "FallbackToLogsOnError"),
            severity_on_drift="info",
        ),
    },

    "Deployment": {
        "spec.replicas": FieldMeta(
            description="Desired pod count. 0 = fully scaled down.",
            k8s_default="1",
            severity_on_drift="critical",
        ),
        "spec.strategy.type": FieldMeta(
            description="Recreate causes downtime; RollingUpdate does not.",
            k8s_default="RollingUpdate",
            valid_values=("Recreate", "RollingUpdate"),
            severity_on_drift="warning",
        ),
        "spec.strategy.rollingUpdate.maxSurge": FieldMeta(
            description="Max extra pods above desired during rolling update.",
            k8s_default="25%",
            severity_on_drift="info",
        ),
        "spec.strategy.rollingUpdate.maxUnavailable": FieldMeta(
            description="Max pods unavailable during rolling update. 0 = never reduce below desired.",
            k8s_default="25%",
            severity_on_drift="warning",
        ),
        "spec.progressDeadlineSeconds": FieldMeta(
            description="Seconds before stalled rollout is marked failed (Progressing=False).",
            k8s_default="600",
            severity_on_drift="critical",
        ),
        "spec.revisionHistoryLimit": FieldMeta(
            description="Number of old ReplicaSets retained for rollback.",
            k8s_default="10",
            severity_on_drift="info",
        ),
        "spec.minReadySeconds": FieldMeta(
            description="Seconds a pod must be Ready before considered available.",
            k8s_default="0",
            severity_on_drift="warning",
        ),
        "spec.paused": FieldMeta(
            description="Paused=true halts rollouts. Often left set accidentally.",
            k8s_default="false",
            severity_on_drift="critical",
        ),
    },

    "StatefulSet": {
        "spec.replicas": FieldMeta(
            description="Desired pod count. Each pod gets stable storage.",
            k8s_default="1",
            severity_on_drift="critical",
        ),
        "spec.podManagementPolicy": FieldMeta(
            description="OrderedReady = sequential startup; Parallel = concurrent.",
            k8s_default="OrderedReady",
            valid_values=("OrderedReady", "Parallel"),
            severity_on_drift="warning",
        ),
        "spec.updateStrategy.type": FieldMeta(
            description="OnDelete = manual updates only; RollingUpdate is automatic.",
            k8s_default="RollingUpdate",
            valid_values=("RollingUpdate", "OnDelete"),
            severity_on_drift="warning",
        ),
        "spec.persistentVolumeClaimRetentionPolicy.whenDeleted": FieldMeta(
            description="PVC fate when StatefulSet is deleted. Retain is safer.",
            k8s_default="Retain",
            valid_values=("Retain", "Delete"),
            severity_on_drift="warning",
        ),
        "spec.persistentVolumeClaimRetentionPolicy.whenScaled": FieldMeta(
            description="PVC fate when StatefulSet is scaled down.",
            k8s_default="Retain",
            valid_values=("Retain", "Delete"),
            severity_on_drift="warning",
        ),
    },

    "DaemonSet": {
        "spec.updateStrategy.type": FieldMeta(
            description="OnDelete = manual; RollingUpdate = automatic node-by-node.",
            k8s_default="RollingUpdate",
            valid_values=("RollingUpdate", "OnDelete"),
            severity_on_drift="warning",
        ),
        "spec.updateStrategy.rollingUpdate.maxUnavailable": FieldMeta(
            description="Max DaemonSet pods unavailable during rolling update.",
            k8s_default="1",
            severity_on_drift="warning",
        ),
        "spec.updateStrategy.rollingUpdate.maxSurge": FieldMeta(
            description="Max extra DaemonSet pods during rolling update.",
            k8s_default="0",
            severity_on_drift="info",
        ),
    },

    "Service": {
        "spec.type": FieldMeta(
            description="ClusterIP=internal; NodePort=node port; LoadBalancer=cloud LB.",
            k8s_default="ClusterIP",
            valid_values=("ClusterIP", "NodePort", "LoadBalancer", "ExternalName"),
            severity_on_drift="warning",
        ),
        "spec.sessionAffinity": FieldMeta(
            description="ClientIP routes same client to same pod. None is stateless.",
            k8s_default="None",
            valid_values=("ClientIP", "None"),
            severity_on_drift="info",
        ),
        "spec.externalTrafficPolicy": FieldMeta(
            description="Local preserves source IP but may cause uneven load.",
            k8s_default="Cluster",
            valid_values=("Cluster", "Local"),
            severity_on_drift="info",
        ),
        "spec.internalTrafficPolicy": FieldMeta(
            description="Local routes to node-local pods only.",
            k8s_default="Cluster",
            valid_values=("Cluster", "Local"),
            severity_on_drift="info",
        ),
    },

    "PersistentVolumeClaim": {
        "spec.accessModes": FieldMeta(
            description="ReadWriteOnce = single node. ReadWriteMany = multi-node (NFS/Ceph).",
            valid_values=("ReadWriteOnce", "ReadOnlyMany", "ReadWriteMany", "ReadWriteOncePod"),
            severity_on_drift="critical",
        ),
        "spec.volumeMode": FieldMeta(
            description="Filesystem = mounted as dir; Block = raw block device.",
            k8s_default="Filesystem",
            valid_values=("Filesystem", "Block"),
            severity_on_drift="warning",
        ),
        "spec.persistentVolumeReclaimPolicy": FieldMeta(
            description="Delete removes volume when PVC is deleted. Retain keeps data.",
            k8s_default="Delete",
            valid_values=("Retain", "Delete", "Recycle"),
            severity_on_drift="warning",
        ),
    },

    "HorizontalPodAutoscaler": {
        "spec.minReplicas": FieldMeta(
            description="HPA will not scale below this. 0 = scale to zero.",
            k8s_default="1",
            severity_on_drift="warning",
        ),
        "spec.maxReplicas": FieldMeta(
            description="HPA will not scale above this. Must be >= minReplicas.",
            severity_on_drift="critical",
        ),
        "spec.targetCPUUtilizationPercentage": FieldMeta(
            description="Target average CPU utilisation across pods for scaling.",
            k8s_default="80",
            severity_on_drift="warning",
        ),
    },

    "ConfigMap": {},    # No K8s-level field constraints
    "Secret": {},
}


def schema_for_kind(kind: str) -> dict[str, FieldMeta]:
    """Return the embedded field schema for a K8s resource kind."""
    return _SCHEMA.get(kind, {})


# ─────────────────────────────────────────────────────────────────────────────
# Live K8s API server enrichment
# ─────────────────────────────────────────────────────────────────────────────

class K8sApiSchema:
    """
    Augments the embedded schema with field descriptions from a live API server.

    Usage:
        schema = K8sApiSchema(api_client)   # pass kubernetes.client.ApiClient
        schema.load()                        # optional — called automatically

    Falls back to the embedded schema on any failure (no network, no cluster).
    """

    # Definition prefixes in K8s OpenAPI spec → resource kind
    _KIND_PREFIXES = frozenset({
        "io.k8s.api.apps.v1.", "io.k8s.api.core.v1.",
        "io.k8s.api.batch.v1.", "io.k8s.api.autoscaling.v2.",
        "io.k8s.api.networking.v1.",
    })

    def __init__(self, api_client=None) -> None:
        self._api_client = api_client
        self._extra: dict[str, dict[str, FieldMeta]] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """
        Try to fetch OpenAPI v2 spec from the API server.
        Returns True on success.
        """
        if self._loaded:
            return True
        if self._api_client is None:
            return False
        try:
            raw, _, _ = self._api_client.call_api(
                "/openapi/v2", "GET",
                response_type=object,
                _return_http_data_only=True,
            )
            self._parse(raw or {})
            self._loaded = True
            log.info("K8sApiSchema: loaded %d extra field(s) from API server",
                     sum(len(v) for v in self._extra.values()))
            return True
        except Exception as exc:
            log.debug("K8sApiSchema: API server unavailable (%s) — using embedded schema", exc)
            return False

    def get(self, kind: str, field_path: str) -> FieldMeta | None:
        extra = self._extra.get(kind, {}).get(field_path)
        if extra:
            return extra
        return _SCHEMA.get(kind, {}).get(field_path)

    def fields_for_kind(self, kind: str) -> dict[str, FieldMeta]:
        base  = _SCHEMA.get(kind, {})
        extra = self._extra.get(kind, {})
        return {**base, **extra}

    # ------------------------------------------------------------------
    # OpenAPI parsing
    # ------------------------------------------------------------------

    def _parse(self, spec: dict) -> None:
        definitions: dict = (
            spec.get("definitions")
            or spec.get("components", {}).get("schemas", {})
            or {}
        )
        for def_name, definition in definitions.items():
            kind = self._extract_kind(def_name)
            if kind and kind in _SCHEMA:
                for field_path, meta in self._extract_fields(definition).items():
                    self._extra.setdefault(kind, {})[field_path] = meta

    def _extract_kind(self, def_name: str) -> str | None:
        for prefix in self._KIND_PREFIXES:
            if def_name.startswith(prefix):
                return def_name[len(prefix):]
        return None

    def _extract_fields(self, definition: dict) -> dict[str, FieldMeta]:
        fields: dict[str, FieldMeta] = {}
        for prop_name, prop_schema in (definition.get("properties") or {}).items():
            desc = prop_schema.get("description", "")
            enum = prop_schema.get("enum", [])
            default = str(prop_schema.get("default", ""))
            if desc or enum:
                fields[f"spec.{prop_name}"] = FieldMeta(
                    description=desc[:160],
                    k8s_default=default,
                    valid_values=tuple(str(v) for v in enum),
                )
        return fields
