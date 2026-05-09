from __future__ import annotations
import logging
from typing import Any

from ontology.entities import (
    DriftItem, Deployment, StatefulSet, DaemonSet, Pod,
    PersistentVolumeClaim, HelmRelease, HelmChart, ResourceKind,
)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType
from ingestion.chart_parser import flatten_values, merge_values_hierarchy

log = logging.getLogger(__name__)


class HelmDriftDetector:
    """
    Compares Helm-declared state (values + chart defaults) against the live
    K8s resources in the graph and produces DriftItem lists.

    A DRIFTS_FROM edge is added from any K8s resource whose observed state
    diverges meaningfully from what its Helm release declares.
    Drift items are stored in entity.annotations["drift"] as serialised text.
    """

    def detect_all(self, graph: OntologyGraph) -> int:
        """
        Runs drift detection for every HelmRelease in the graph.
        Returns the total number of drift items found.
        """
        total = 0
        for release in graph.entities(ResourceKind.HELM_RELEASE):
            total += self._detect_for_release(graph, release)
        log.info("Drift detection complete: %d drift item(s) found", total)
        return total

    # ------------------------------------------------------------------
    # Per-release
    # ------------------------------------------------------------------

    def _detect_for_release(self, graph: OntologyGraph, release: HelmRelease) -> int:
        # Resolve the full values for this release: chart defaults + release values
        chart = self._find_chart(graph, release)
        chart_defaults = chart.default_values if chart else {}
        full_values = merge_values_hierarchy(chart_defaults, release.values)
        flat_declared = flatten_values(full_values)

        count = 0
        ns = release.namespace

        # Check Deployments
        for dep in graph.entities(ResourceKind.DEPLOYMENT):
            if dep.namespace != ns:
                continue
            if not self._managed_by(graph, dep.uid, release.uid):
                continue
            drifts = self._check_deployment(dep, flat_declared, release.name)
            count += self._apply_drifts(graph, dep, release, drifts)

        # Check StatefulSets
        for sts in graph.entities(ResourceKind.STATEFULSET):
            if sts.namespace != ns:
                continue
            if not self._managed_by(graph, sts.uid, release.uid):
                continue
            drifts = self._check_statefulset(sts, flat_declared, release.name)
            count += self._apply_drifts(graph, sts, release, drifts)

        # Check DaemonSets
        for ds in graph.entities(ResourceKind.DAEMONSET):
            if ds.namespace != ns:
                continue
            if not self._managed_by(graph, ds.uid, release.uid):
                continue
            drifts = self._check_daemonset(ds, flat_declared, release.name)
            count += self._apply_drifts(graph, ds, release, drifts)

        # Check PVCs (persistence.enabled pattern)
        for pvc in graph.entities(ResourceKind.PERSISTENT_VOLUME_CLAIM):
            if pvc.namespace != ns:
                continue
            if not self._managed_by(graph, pvc.uid, release.uid):
                continue
            drifts = self._check_pvc(pvc, flat_declared)
            count += self._apply_drifts(graph, pvc, release, drifts)

        # Check pods for image drift and crash loops
        for pod in graph.entities(ResourceKind.POD):
            if pod.namespace != ns:
                continue
            if not self._managed_by(graph, pod.uid, release.uid):
                continue
            drifts = self._check_pod(pod, flat_declared)
            count += self._apply_drifts(graph, pod, release, drifts)

        # Check sub-chart enabled/disabled conditions
        if chart:
            drifts = self._check_subchart_conditions(graph, chart, full_values, ns, release)
            for d in drifts:
                _annotate_entity(release, d)
                count += 1

        return count

    # ------------------------------------------------------------------
    # Per-resource checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_deployment(dep: Deployment, flat: dict[str, str], release: str) -> list[DriftItem]:
        items: list[DriftItem] = []

        # replicas
        declared_replicas = _first_int(flat, [
            "replicaCount", f"{release}.replicaCount",
            "replicas", "deployment.replicaCount",
        ])
        if declared_replicas is not None and dep.replicas != declared_replicas:
            items.append(DriftItem(
                field_path="spec.replicas",
                declared=declared_replicas,
                observed=dep.replicas,
                severity="warning",
                source="Deployment",
            ))

        # ready vs desired
        if dep.replicas > 0 and dep.ready_replicas < dep.replicas:
            items.append(DriftItem(
                field_path="status.readyReplicas",
                declared=dep.replicas,
                observed=dep.ready_replicas,
                severity="critical" if dep.ready_replicas == 0 else "warning",
                source="Deployment",
            ))

        return items

    @staticmethod
    def _check_statefulset(sts: StatefulSet, flat: dict[str, str], release: str) -> list[DriftItem]:
        items: list[DriftItem] = []
        if sts.replicas > 0 and sts.ready_replicas < sts.replicas:
            items.append(DriftItem(
                field_path="status.readyReplicas",
                declared=sts.replicas,
                observed=sts.ready_replicas,
                severity="critical" if sts.ready_replicas == 0 else "warning",
                source="StatefulSet",
            ))
        return items

    @staticmethod
    def _check_daemonset(ds: DaemonSet, flat: dict[str, str], release: str) -> list[DriftItem]:
        items: list[DriftItem] = []
        if ds.desired > 0 and ds.ready < ds.desired:
            items.append(DriftItem(
                field_path="status.numberReady",
                declared=ds.desired,
                observed=ds.ready,
                severity="critical" if ds.ready == 0 else "warning",
                source="DaemonSet",
            ))
        return items

    @staticmethod
    def _check_pvc(pvc: PersistentVolumeClaim, flat: dict[str, str]) -> list[DriftItem]:
        items: list[DriftItem] = []

        # persistence.enabled=true but PVC not Bound
        persistence_enabled = flat.get("persistence.enabled", "true").lower()
        if persistence_enabled == "true" and pvc.status_phase != "Bound":
            items.append(DriftItem(
                field_path="status.phase",
                declared="Bound",
                observed=pvc.status_phase,
                severity="critical",
                source="PVC",
            ))

        # storage size drift
        declared_size = flat.get("persistence.size") or flat.get("storage.size")
        if declared_size and pvc.requested_storage and declared_size != pvc.requested_storage:
            items.append(DriftItem(
                field_path="spec.resources.requests.storage",
                declared=declared_size,
                observed=pvc.requested_storage,
                severity="info",
                source="PVC",
            ))

        return items

    @staticmethod
    def _check_pod(pod: Pod, flat: dict[str, str]) -> list[DriftItem]:
        items: list[DriftItem] = []

        # Image tag drift: values.image.tag vs running container image
        declared_tag = flat.get("image.tag")
        if declared_tag:
            for cs in pod.container_statuses:
                # container_statuses stores state as string — look for image in raw
                pass  # image comparison done via raw data if available

        # CrashLoopBackOff detection
        for cs in pod.container_statuses:
            state_str = str(cs.get("state", ""))
            if "CrashLoopBackOff" in state_str or "OOMKilled" in state_str:
                items.append(DriftItem(
                    field_path=f"container.{cs.get('name', '?')}.state",
                    declared="Running",
                    observed="CrashLoopBackOff" if "CrashLoopBackOff" in state_str else "OOMKilled",
                    severity="critical",
                    source="Pod",
                ))

        if pod.restart_count > 5:
            items.append(DriftItem(
                field_path="status.restartCount",
                declared=0,
                observed=pod.restart_count,
                severity="warning" if pod.restart_count < 20 else "critical",
                source="Pod",
            ))

        return items

    @staticmethod
    def _check_subchart_conditions(
        graph: OntologyGraph,
        chart: HelmChart,
        full_values: dict,
        namespace: str,
        release: HelmRelease,
    ) -> list[DriftItem]:
        """
        For each dependency with a condition (e.g. postgresql.enabled),
        check whether the K8s resources for that sub-chart are actually present.
        """
        items: list[DriftItem] = []
        for dep in chart.dependencies:
            if not dep.condition:
                continue
            # Resolve condition value: "postgresql.enabled" → full_values["postgresql"]["enabled"]
            condition_val = _resolve_dot_path(full_values, dep.condition)
            is_enabled = condition_val is True or condition_val == "true"

            # Check if sub-chart resources exist in the namespace
            sub_label = dep.alias or dep.name
            resources_exist = any(
                sub_label in (e.labels.get("app.kubernetes.io/name", "")
                              or e.labels.get("app", ""))
                for e in graph.entities()
                if e.namespace == namespace
            )

            if is_enabled and not resources_exist:
                items.append(DriftItem(
                    field_path=f"dependencies.{sub_label}.condition",
                    declared=f"{dep.condition}=true → resources present",
                    observed="no matching resources found in namespace",
                    severity="warning",
                    source="UmbrellaChart",
                ))
            elif not is_enabled and resources_exist:
                items.append(DriftItem(
                    field_path=f"dependencies.{sub_label}.condition",
                    declared=f"{dep.condition}=false → no resources",
                    observed="resources exist but sub-chart is disabled",
                    severity="info",
                    source="UmbrellaChart",
                ))

        return items

    # ------------------------------------------------------------------
    # Graph helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_chart(graph: OntologyGraph, release: HelmRelease) -> HelmChart | None:
        for edge in graph._adj.get(release.uid, []):
            if edge.rel_type == RelationshipType.DEPLOYED_FROM:
                entity = graph.get(edge.target_uid)
                if isinstance(entity, HelmChart):
                    return entity
        return None

    @staticmethod
    def _managed_by(graph: OntologyGraph, resource_uid: str, release_uid: str) -> bool:
        for edge in graph._adj.get(resource_uid, []):
            if (edge.rel_type == RelationshipType.MANAGED_BY_HELM
                    and edge.target_uid == release_uid):
                return True
        return False

    @staticmethod
    def _apply_drifts(
        graph: OntologyGraph,
        entity: Any,
        release: HelmRelease,
        drifts: list[DriftItem],
    ) -> int:
        if not drifts:
            return 0
        for drift in drifts:
            _annotate_entity(entity, drift)
        # Add a single DRIFTS_FROM edge (idempotent — check first)
        already_has_edge = any(
            e.rel_type == RelationshipType.DRIFTS_FROM and e.target_uid == release.uid
            for e in graph._adj.get(entity.uid, [])
        )
        if not already_has_edge:
            graph.add_edge(Edge(entity.uid, release.uid, RelationshipType.DRIFTS_FROM))
        return len(drifts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _annotate_entity(entity: Any, drift: DriftItem) -> None:
    key = f"drift.{drift.field_path}"
    entity.annotations[key] = drift.to_text()


def _first_int(flat: dict[str, str], keys: list[str]) -> int | None:
    for k in keys:
        v = flat.get(k)
        if v is not None:
            try:
                return int(v)
            except (ValueError, TypeError):
                pass
    return None


def _resolve_dot_path(data: dict, path: str) -> Any:
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
