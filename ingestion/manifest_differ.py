"""
ManifestDiffer — diffs Helm-rendered manifests against live K8s entities.

Detects:
  MISSING   rendered resource not found in the cluster       (critical)
  ORPHANED  cluster resource not in rendered manifest         (warning)
  REPLICAS  spec.replicas mismatch                            (warning/critical)
  IMAGE     container image tag mismatch                      (warning)
  RESOURCES CPU/memory request or limit mismatch              (info)
  ENV       selected environment variable value mismatch      (info)

Results are written as `gitops.*` annotations on matched entities and returned
as a flat list of DriftItem for upstream consumers.
"""
from __future__ import annotations

import logging

from ontology.entities import DriftItem, K8sEntity
from ontology.graph import OntologyGraph

log = logging.getLogger(__name__)

# Resource kinds we track for missing/orphan detection
_TRACKED_KINDS = frozenset({
    "Deployment", "StatefulSet", "DaemonSet",
    "Service", "Ingress", "ServiceAccount",
    "ConfigMap", "PersistentVolumeClaim",
})

# Env var names that are high-value to diff (add more as needed)
_SENSITIVE_ENV_KEYS = frozenset({
    "DATABASE_URL", "REDIS_URL", "KAFKA_BROKERS",
    "LOG_LEVEL", "REPLICAS", "PORT",
})


class ManifestDiffer:
    """
    Compare rendered Kubernetes manifests against the OntologyGraph.

    Parameters
    ----------
    track_orphans:  Also flag cluster resources absent from rendered output.
    """

    def __init__(self, track_orphans: bool = True) -> None:
        self.track_orphans = track_orphans

    def diff(
        self,
        rendered: list[dict],
        graph: OntologyGraph,
        release_uid: str = "",
    ) -> list[DriftItem]:
        """
        Returns all DriftItems found.
        Side-effect: writes gitops.* annotations onto entities.
        """
        all_drifts: list[DriftItem] = []
        matched_uids: set[str] = set()

        for manifest in rendered:
            kind = manifest.get("kind", "")
            meta = manifest.get("metadata", {})
            name = meta.get("name", "")
            namespace = meta.get("namespace", "")

            entity = _find_entity(graph, kind, name, namespace)

            if entity is None:
                if kind in _TRACKED_KINDS:
                    item = DriftItem(
                        field_path=f"{kind}.{namespace}.{name}",
                        declared="present",
                        observed="missing",
                        severity="critical",
                        source="gitops",
                    )
                    all_drifts.append(item)
                    _annotate_release(graph, release_uid,
                                      f"gitops.missing.{kind}.{name}", item.to_text())
                    log.info("gitops: %s/%s/%s rendered but absent from cluster",
                             kind, namespace, name)
                continue

            matched_uids.add(entity.uid)
            drifts = self._compare(manifest, entity)
            for d in drifts:
                all_drifts.append(d)
                entity.annotations[f"gitops.{d.field_path}"] = d.to_text()
                log.info("gitops drift on %s: %s", entity.uid, d.to_text())

        # Orphan detection: tracked kinds in cluster but not in rendered output
        if self.track_orphans:
            for entity in graph.entities():
                if (entity.kind.value in _TRACKED_KINDS
                        and entity.uid not in matched_uids
                        and entity.labels.get("app.kubernetes.io/managed-by") == "Helm"):
                    item = DriftItem(
                        field_path=f"{entity.kind.value}.{entity.namespace}.{entity.name}",
                        declared="absent",
                        observed="present",
                        severity="warning",
                        source="gitops",
                    )
                    all_drifts.append(item)
                    entity.annotations["gitops.orphan"] = item.to_text()

        return all_drifts

    # ------------------------------------------------------------------

    def _compare(self, manifest: dict, entity: K8sEntity) -> list[DriftItem]:
        kind = manifest.get("kind", "")
        spec = manifest.get("spec", {})
        drifts: list[DriftItem] = []

        # Replica-bearing kinds
        if kind in ("Deployment", "StatefulSet"):
            drifts += _diff_replicas(spec, entity)

        # Pod template containers (Deployment/StatefulSet) or bare Pod
        if kind == "Pod":
            drifts += _diff_containers(spec, entity)
        else:
            pod_spec = spec.get("template", {}).get("spec", {})
            if pod_spec:
                drifts += _diff_containers(pod_spec, entity)

        # Service ports
        if kind == "Service":
            drifts += _diff_service_ports(spec, entity)

        return drifts


# ─────────────────────────────────────────────────────────────────────────────
# Field-level diff helpers
# ─────────────────────────────────────────────────────────────────────────────

def _diff_replicas(spec: dict, entity: K8sEntity) -> list[DriftItem]:
    rendered = spec.get("replicas")
    if rendered is None or not hasattr(entity, "replicas"):
        return []
    observed = entity.replicas
    if rendered == observed:
        return []
    delta = abs(rendered - observed)
    return [DriftItem(
        field_path="spec.replicas",
        declared=rendered,
        observed=observed,
        severity="critical" if delta > 1 else "warning",
        source="gitops",
    )]


def _diff_containers(pod_spec: dict, entity: K8sEntity) -> list[DriftItem]:
    drifts: list[DriftItem] = []
    containers: list[dict] = pod_spec.get("containers") or []

    # Build map of running container state from entity annotations
    running: dict[str, dict] = {}
    if hasattr(entity, "container_statuses") and entity.container_statuses:
        for cs in entity.container_statuses:
            if isinstance(cs, dict):
                running[cs.get("name", "")] = cs

    for c in containers:
        name = c.get("name", "")
        rendered_image = c.get("image", "")

        # Image drift
        observed_image = (running.get(name) or {}).get("image", "")
        if rendered_image and observed_image and rendered_image != observed_image:
            drifts.append(DriftItem(
                field_path=f"container.{name}.image",
                declared=rendered_image,
                observed=observed_image,
                severity="warning",
                source="gitops",
            ))

        # Resource requests/limits
        drifts += _diff_resources(name, c.get("resources", {}), entity)

        # Selected env vars
        drifts += _diff_env(name, c.get("env") or [], entity)

    return drifts


def _diff_resources(
    c_name: str, rendered_resources: dict, entity: K8sEntity
) -> list[DriftItem]:
    # Without the full raw K8s spec on entity we can only report if both sides
    # have explicit values. Skip silently when one side is unknown.
    drifts: list[DriftItem] = []
    limits = rendered_resources.get("limits") or {}
    requests = rendered_resources.get("requests") or {}

    # Look for resource annotations set by HelmDriftDetector or prior gitops runs
    for field, rendered_val in {**limits, **requests}.items():
        ann_key = f"gitops.resources.{c_name}.{field}"
        observed_val = entity.annotations.get(ann_key)
        if observed_val and str(rendered_val) != str(observed_val):
            drifts.append(DriftItem(
                field_path=f"container.{c_name}.resources.{field}",
                declared=rendered_val,
                observed=observed_val,
                severity="info",
                source="gitops",
            ))
    return drifts


def _diff_env(c_name: str, rendered_env: list[dict], entity: K8sEntity) -> list[DriftItem]:
    drifts: list[DriftItem] = []
    for entry in rendered_env:
        var_name = entry.get("name", "")
        if var_name not in _SENSITIVE_ENV_KEYS:
            continue
        rendered_val = entry.get("value")
        if rendered_val is None:
            continue
        ann_key = f"env.{c_name}.{var_name}"
        observed_val = entity.annotations.get(ann_key)
        if observed_val is not None and str(rendered_val) != str(observed_val):
            drifts.append(DriftItem(
                field_path=f"container.{c_name}.env.{var_name}",
                declared=rendered_val,
                observed=observed_val,
                severity="info",
                source="gitops",
            ))
    return drifts


def _diff_service_ports(spec: dict, entity: K8sEntity) -> list[DriftItem]:
    drifts: list[DriftItem] = []
    rendered_ports: list[dict] = spec.get("ports") or []
    if not rendered_ports or not hasattr(entity, "ports"):
        return drifts
    observed_ports: list[dict] = entity.ports or []
    observed_set = {(p.get("port"), p.get("protocol", "TCP")) for p in observed_ports}
    for p in rendered_ports:
        key = (p.get("containerPort") or p.get("port"), p.get("protocol", "TCP"))
        if key not in observed_set:
            drifts.append(DriftItem(
                field_path=f"spec.ports.{key[0]}",
                declared=str(key),
                observed="missing",
                severity="warning",
                source="gitops",
            ))
    return drifts


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _find_entity(
    graph: OntologyGraph, kind: str, name: str, namespace: str
) -> K8sEntity | None:
    for entity in graph.entities():
        if entity.name != name:
            continue
        if entity.kind.value != kind:
            continue
        if namespace and entity.namespace and entity.namespace != namespace:
            continue
        return entity
    return None


def _annotate_release(
    graph: OntologyGraph, release_uid: str, key: str, value: str
) -> None:
    if not release_uid:
        return
    entity = graph.get(release_uid)
    if entity:
        entity.annotations[key] = value
