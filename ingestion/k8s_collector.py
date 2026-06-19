from __future__ import annotations
import logging

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client import ApiException

import config as cfg
from ontology.discovery import APIServerDiscovery, _TYPED_KINDS
from ontology.dynamic_entity import GenericEntity
from ontology.entities import (
    Namespace, Node, Pod, Deployment, StatefulSet, DaemonSet, ReplicaSet,
    Service, Ingress, ConfigMap, Secret, PersistentVolume,
    PersistentVolumeClaim, ServiceAccount, K8sEvent,
)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType
from ontology.version import KubeVersion, detect_version

log = logging.getLogger(__name__)


def _ts(obj):
    from datetime import datetime
    if obj is None:
        return None
    if isinstance(obj, datetime):
        return obj
    return None


class K8sCollector:
    """
    Collects cluster state by combining:
    - Typed extraction for well-known core resources (Pod, Deployment, etc.)
    - Dynamic extraction via APIServerDiscovery for every other kind the
      server exposes (CRDs, API extensions, operators, etc.)

    All API version choices are driven by the server's reported version so
    the same code runs against K8s 1.16 through 1.30+ (K3s included).
    """

    def __init__(
        self,
        kubeconfig: str | None = None,
        context: str | None = None,
        impersonate_user: str | None = None,
        impersonate_groups: list[str] | None = None,
    ) -> None:
        kc = kubeconfig or cfg.KUBECONFIG
        ctx = context or cfg.KUBE_CONTEXT

        if kc:
            k8s_config.load_kube_config(config_file=kc, context=ctx)
        else:
            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config(context=ctx)

        # RBAC-aware scoping: run all API calls as a specific user/groups via
        # Kubernetes user impersonation (Impersonate-User / -Group headers).
        # Lets one ServiceAccount analyse per-tenant with the tenant's own RBAC,
        # rather than the collector's broad privileges. Configured per-call or
        # via KUBE_IMPERSONATE_USER / KUBE_IMPERSONATE_GROUPS.
        user = impersonate_user or cfg.KUBE_IMPERSONATE_USER
        groups = impersonate_groups or cfg.KUBE_IMPERSONATE_GROUPS
        self._api_client = k8s_client.ApiClient()
        if user or groups:
            if user:
                # K8s user impersonation: the apiserver evaluates RBAC as `user`.
                self._api_client.set_default_header("Impersonate-User", user)
            for grp in groups or []:
                self._api_client.set_default_header("Impersonate-Group", grp)
            log.info("RBAC impersonation: user=%s groups=%s", user, groups or [])

        # Detect server version first — drives all API version choices below
        self.kube_version: KubeVersion = detect_version(self._api_client)
        log.info("Kubernetes server version: %s", self.kube_version)
        for note in self.kube_version.changelog_notes():
            log.debug(note)

        self._core = k8s_client.CoreV1Api(self._api_client)
        self._apps = k8s_client.AppsV1Api(self._api_client)

        # Ingress API depends on server version
        if self.kube_version.supports_networking_v1_ingress:
            self._ingress_api = k8s_client.NetworkingV1Api(self._api_client)
            self._ingress_lister = self._list_ingress_v1
        else:
            # 1.14–1.18: networking.k8s.io/v1beta1
            self._ingress_api = k8s_client.NetworkingV1beta1Api(self._api_client)
            self._ingress_lister = self._list_ingress_v1beta1

        self._discovery = APIServerDiscovery(self._api_client)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def collect(self, namespaces: list[str] | None = None) -> OntologyGraph:
        ns_filter = namespaces or cfg.KUBE_NAMESPACES or None
        graph = OntologyGraph(server_version=self.kube_version)

        # 1. Typed core resources (rich field extraction + relationship wiring)
        log.info("Collecting core typed resources…")
        self._collect_namespaces(graph, ns_filter=ns_filter)
        if not ns_filter:
            self._collect_nodes(graph)

        all_ns = ns_filter or [ns.name for ns in graph.entities("Namespace")]
        for ns in all_ns:
            self._collect_pods(graph, ns)
            self._collect_deployments(graph, ns)
            self._collect_statefulsets(graph, ns)
            self._collect_daemonsets(graph, ns)
            self._collect_replicasets(graph, ns)
            self._collect_services(graph, ns)
            self._collect_ingresses(graph, ns)
            self._collect_configmaps(graph, ns)
            self._collect_secrets(graph, ns)
            self._collect_pvcs(graph, ns)
            self._collect_serviceaccounts(graph, ns)
            self._collect_events(graph, ns)

        self._collect_pvs(graph)

        # 2. Dynamic discovery — everything else the server exposes
        log.info("Running API server discovery for non-core resource kinds…")
        all_resources = self._discovery.list_all_resources(
            skip_kinds=cfg.KUBE_SKIP_KINDS | _TYPED_KINDS
        )
        log.info(self._discovery.vocabulary_summary(all_resources))

        seen_uids: set[str] = {e.uid for e in graph.entities()}
        dynamic_count = 0
        for resource_info in all_resources:
            for entity in self._discovery.fetch_resources(resource_info, namespaces=ns_filter):
                if entity.uid not in seen_uids:
                    graph.add_entity(entity)
                    seen_uids.add(entity.uid)
                    dynamic_count += 1
                    self._wire_namespace_edge(graph, entity)

        log.info("Dynamic resources added: %d", dynamic_count)
        log.info(graph.summary())
        return graph

    # ------------------------------------------------------------------
    # Namespace-edge helper for dynamic entities
    # ------------------------------------------------------------------

    def _wire_namespace_edge(self, graph: OntologyGraph, entity: GenericEntity) -> None:
        if not entity.namespace:
            return
        ns = self._find_by_name(graph, entity.namespace, None, "Namespace")
        if ns:
            graph.add_edge(Edge(entity.uid, ns.uid, RelationshipType.IN_NAMESPACE))

    # ------------------------------------------------------------------
    # Typed collectors (unchanged logic, now use cfg for auth)
    # ------------------------------------------------------------------

    def _collect_namespaces(self, graph: OntologyGraph, ns_filter: list[str] | None = None) -> list:
        entities = []
        for item in self._core.list_namespace().items:
            if ns_filter and item.metadata.name not in ns_filter:
                continue
            e = Namespace(
                uid=item.metadata.uid,
                name=item.metadata.name,
                namespace=None,
                labels=item.metadata.labels or {},
                annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                phase=item.status.phase or "Active",
                raw=item.to_dict(),
            )
            graph.add_entity(e)
            entities.append(e)
        return entities

    def _collect_nodes(self, graph: OntologyGraph) -> None:
        for item in self._core.list_node().items:
            alloc = item.status.allocatable or {}
            conditions = [{"type": c.type, "status": c.status}
                          for c in (item.status.conditions or [])]
            ready = any(c["type"] == "Ready" and c["status"] == "True"
                        for c in conditions)
            e = Node(
                uid=item.metadata.uid,
                name=item.metadata.name,
                namespace=None,
                labels=item.metadata.labels or {},
                annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                ready=ready,
                allocatable_cpu=alloc.get("cpu", ""),
                allocatable_memory=alloc.get("memory", ""),
                conditions=conditions,
                taints=[t.to_dict() for t in (item.spec.taints or [])],
                raw=item.to_dict(),
            )
            graph.add_entity(e)

    def _collect_pods(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._core.list_namespaced_pod(namespace).items
        except ApiException as exc:
            log.warning("Cannot list pods in %s: %s", namespace, exc)
            return

        ns_entity = self._find_by_name(graph, namespace, None, "Namespace")
        for item in items:
            owner_kind, owner_name = "", ""
            for ref in (item.metadata.owner_references or []):
                owner_kind, owner_name = ref.kind, ref.name
                break
            restart_count = sum(
                (cs.restart_count or 0) for cs in (item.status.container_statuses or [])
            )
            container_statuses = [
                {"name": cs.name, "ready": cs.ready,
                 "restart_count": cs.restart_count, "state": str(cs.state)}
                for cs in (item.status.container_statuses or [])
            ]
            e = Pod(
                uid=item.metadata.uid,
                name=item.metadata.name,
                namespace=namespace,
                labels=item.metadata.labels or {},
                annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                phase=item.status.phase or "Unknown",
                node_name=item.spec.node_name or "",
                restart_count=restart_count,
                container_statuses=container_statuses,
                owner_ref_kind=owner_kind,
                owner_ref_name=owner_name,
                raw=item.to_dict(),
            )
            graph.add_entity(e)
            if ns_entity:
                graph.add_edge(Edge(e.uid, ns_entity.uid, RelationshipType.IN_NAMESPACE))
            if item.spec.node_name:
                node = self._find_by_name(graph, item.spec.node_name, None, "Node")
                if node:
                    graph.add_edge(Edge(e.uid, node.uid, RelationshipType.RUNS_ON))
            for vol in (item.spec.volumes or []):
                if vol.config_map:
                    self._lazy_edge(graph, e.uid, namespace, vol.config_map.name,
                                    "ConfigMap", RelationshipType.MOUNTS_CONFIGMAP)
                if vol.secret:
                    self._lazy_edge(graph, e.uid, namespace, vol.secret.secret_name,
                                    "Secret", RelationshipType.MOUNTS_SECRET)
                if vol.persistent_volume_claim:
                    self._lazy_edge(graph, e.uid, namespace,
                                    vol.persistent_volume_claim.claim_name,
                                    "PersistentVolumeClaim", RelationshipType.USES_PVC)
            if item.spec.service_account_name:
                self._lazy_edge(graph, e.uid, namespace,
                                item.spec.service_account_name,
                                "ServiceAccount", RelationshipType.USES_SERVICE_ACCOUNT)

    def _collect_deployments(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._apps.list_namespaced_deployment(namespace).items
        except ApiException as exc:
            log.warning("Cannot list deployments in %s: %s", namespace, exc)
            return
        ns_entity = self._find_by_name(graph, namespace, None, "Namespace")
        for item in items:
            s = item.status
            ann = dict(item.metadata.annotations or {})
            for c in (item.spec.template.spec.containers or []):
                if c.image:
                    ann[f"spec.container.{c.name}.image"] = c.image
            e = Deployment(
                uid=item.metadata.uid,
                name=item.metadata.name,
                namespace=namespace,
                labels=item.metadata.labels or {},
                annotations=ann,
                created_at=_ts(item.metadata.creation_timestamp),
                replicas=item.spec.replicas or 0,
                ready_replicas=s.ready_replicas or 0,
                available_replicas=s.available_replicas or 0,
                strategy=item.spec.strategy.type if item.spec.strategy else "RollingUpdate",
                selector=item.spec.selector.match_labels or {},
                raw=item.to_dict(),
            )
            graph.add_entity(e)
            if ns_entity:
                graph.add_edge(Edge(e.uid, ns_entity.uid, RelationshipType.IN_NAMESPACE))

    def _collect_statefulsets(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._apps.list_namespaced_stateful_set(namespace).items
        except ApiException as exc:
            log.warning("Cannot list statefulsets in %s: %s", namespace, exc)
            return
        for item in items:
            sts_ann = dict(item.metadata.annotations or {})
            for c in (item.spec.template.spec.containers or []):
                if c.image:
                    sts_ann[f"spec.container.{c.name}.image"] = c.image
            graph.add_entity(StatefulSet(
                uid=item.metadata.uid, name=item.metadata.name, namespace=namespace,
                labels=item.metadata.labels or {}, annotations=sts_ann,
                created_at=_ts(item.metadata.creation_timestamp),
                replicas=item.spec.replicas or 0, ready_replicas=item.status.ready_replicas or 0,
                selector=item.spec.selector.match_labels or {}, raw=item.to_dict(),
            ))

    def _collect_daemonsets(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._apps.list_namespaced_daemon_set(namespace).items
        except ApiException as exc:
            log.warning("Cannot list daemonsets in %s: %s", namespace, exc)
            return
        for item in items:
            graph.add_entity(DaemonSet(
                uid=item.metadata.uid, name=item.metadata.name, namespace=namespace,
                labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                desired=item.status.desired_number_scheduled or 0,
                ready=item.status.number_ready or 0,
                selector=item.spec.selector.match_labels or {}, raw=item.to_dict(),
            ))

    def _collect_replicasets(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._apps.list_namespaced_replica_set(namespace).items
        except ApiException as exc:
            log.warning("Cannot list replicasets in %s: %s", namespace, exc)
            return
        for item in items:
            owner_name = next(
                (ref.name for ref in (item.metadata.owner_references or [])
                 if ref.kind == "Deployment"), ""
            )
            e = ReplicaSet(
                uid=item.metadata.uid, name=item.metadata.name, namespace=namespace,
                labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                replicas=item.spec.replicas or 0, ready_replicas=item.status.ready_replicas or 0,
                owner_ref_name=owner_name,
                selector=item.spec.selector.match_labels or {}, raw=item.to_dict(),
            )
            graph.add_entity(e)
            if owner_name:
                deployment = self._find_by_name(graph, owner_name, namespace, "Deployment")
                if deployment:
                    graph.add_edge(Edge(deployment.uid, e.uid, RelationshipType.OWNS))

    def _collect_services(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._core.list_namespaced_service(namespace).items
        except ApiException as exc:
            log.warning("Cannot list services in %s: %s", namespace, exc)
            return
        for item in items:
            ports = [{"port": p.port, "targetPort": str(p.target_port), "protocol": p.protocol}
                     for p in (item.spec.ports or [])]
            e = Service(
                uid=item.metadata.uid, name=item.metadata.name, namespace=namespace,
                labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                service_type=item.spec.type or "ClusterIP",
                cluster_ip=item.spec.cluster_ip or "",
                ports=ports, selector=item.spec.selector or {}, raw=item.to_dict(),
            )
            graph.add_entity(e)
            if item.spec.selector:
                from ontology.entities import ResourceKind
                for pod in graph.entities(ResourceKind.POD):
                    if pod.namespace == namespace and self._labels_match(item.spec.selector, pod.labels):
                        graph.add_edge(Edge(e.uid, pod.uid, RelationshipType.EXPOSES))

    def _collect_ingresses(self, graph: OntologyGraph, namespace: str) -> None:
        self._ingress_lister(graph, namespace)

    def _list_ingress_v1(self, graph: OntologyGraph, namespace: str) -> None:
        """networking.k8s.io/v1 — K8s >= 1.19."""
        try:
            items = self._ingress_api.list_namespaced_ingress(namespace).items
        except ApiException as exc:
            log.warning("Cannot list ingresses (v1) in %s: %s", namespace, exc)
            return
        for item in items:
            ingress_class = item.spec.ingress_class_name or (
                item.metadata.annotations or {}).get("kubernetes.io/ingress.class", "")
            rules = []
            for rule in (item.spec.rules or []):
                if rule.http:
                    for path in (rule.http.paths or []):
                        svc_name = path.backend.service.name if path.backend.service else ""
                        rules.append({"host": rule.host or "", "path": path.path or "/",
                                      "service": svc_name})
            self._add_ingress(graph, item, namespace, rules, ingress_class)

    def _list_ingress_v1beta1(self, graph: OntologyGraph, namespace: str) -> None:
        """networking.k8s.io/v1beta1 — K8s < 1.19 (removed in 1.22)."""
        try:
            items = self._ingress_api.list_namespaced_ingress(namespace).items
        except ApiException as exc:
            log.warning("Cannot list ingresses (v1beta1) in %s: %s", namespace, exc)
            return
        for item in items:
            ingress_class = (item.metadata.annotations or {}).get(
                "kubernetes.io/ingress.class", "")
            rules = []
            for rule in (item.spec.rules or []):
                if rule.http:
                    for path in (rule.http.paths or []):
                        # v1beta1 backend: serviceName / servicePort (no nested .service)
                        svc_name = getattr(path.backend, "service_name", "") or ""
                        rules.append({"host": rule.host or "", "path": path.path or "/",
                                      "service": svc_name})
            self._add_ingress(graph, item, namespace, rules, ingress_class)

    def _add_ingress(self, graph, item, namespace, rules, ingress_class) -> None:
        e = Ingress(
            uid=item.metadata.uid, name=item.metadata.name, namespace=namespace,
            labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
            created_at=_ts(item.metadata.creation_timestamp),
            rules=rules, ingress_class=ingress_class, raw=item.to_dict(),
        )
        graph.add_entity(e)
        for rule in rules:
            svc = self._find_by_name(graph, rule.get("service", ""), namespace, "Service")
            if svc:
                graph.add_edge(Edge(e.uid, svc.uid, RelationshipType.ROUTES_TO))

    def _collect_configmaps(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._core.list_namespaced_config_map(namespace).items
        except ApiException as exc:
            log.warning("Cannot list configmaps in %s: %s", namespace, exc)
            return
        for item in items:
            graph.add_entity(ConfigMap(
                uid=item.metadata.uid, name=item.metadata.name, namespace=namespace,
                labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                data_keys=list((item.data or {}).keys()), raw=item.to_dict(),
            ))

    def _collect_secrets(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._core.list_namespaced_secret(namespace).items
        except ApiException as exc:
            log.warning("Cannot list secrets in %s: %s", namespace, exc)
            return
        for item in items:
            graph.add_entity(Secret(
                uid=item.metadata.uid, name=item.metadata.name, namespace=namespace,
                labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                secret_type=item.type or "Opaque",
                data_keys=list((item.data or {}).keys()), raw={},
            ))

    def _collect_pvs(self, graph: OntologyGraph) -> None:
        for item in self._core.list_persistent_volume().items:
            capacity = (item.spec.capacity or {}).get("storage", "")
            graph.add_entity(PersistentVolume(
                uid=item.metadata.uid, name=item.metadata.name, namespace=None,
                labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                capacity=capacity, access_modes=item.spec.access_modes or [],
                reclaim_policy=item.spec.persistent_volume_reclaim_policy or "Retain",
                status_phase=item.status.phase or "Available",
                storage_class=item.spec.storage_class_name or "", raw=item.to_dict(),
            ))

    def _collect_pvcs(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._core.list_namespaced_persistent_volume_claim(namespace).items
        except ApiException as exc:
            log.warning("Cannot list pvcs in %s: %s", namespace, exc)
            return
        for item in items:
            req_storage = ""
            if item.spec.resources and item.spec.resources.requests:
                req_storage = item.spec.resources.requests.get("storage", "")
            e = PersistentVolumeClaim(
                uid=item.metadata.uid, name=item.metadata.name, namespace=namespace,
                labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                requested_storage=req_storage, access_modes=item.spec.access_modes or [],
                status_phase=item.status.phase or "Pending",
                storage_class=item.spec.storage_class_name or "",
                volume_name=item.spec.volume_name or "", raw=item.to_dict(),
            )
            graph.add_entity(e)
            if item.spec.volume_name:
                pv = self._find_by_name(graph, item.spec.volume_name, None, "PersistentVolume")
                if pv:
                    graph.add_edge(Edge(e.uid, pv.uid, RelationshipType.BINDS_PV))

    def _collect_serviceaccounts(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._core.list_namespaced_service_account(namespace).items
        except ApiException as exc:
            log.warning("Cannot list serviceaccounts in %s: %s", namespace, exc)
            return
        for item in items:
            graph.add_entity(ServiceAccount(
                uid=item.metadata.uid, name=item.metadata.name, namespace=namespace,
                labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                secrets=[s.name for s in (item.secrets or [])], raw=item.to_dict(),
            ))

    def _collect_events(self, graph: OntologyGraph, namespace: str) -> None:
        try:
            items = self._core.list_namespaced_event(namespace).items
        except ApiException as exc:
            log.warning("Cannot list events in %s: %s", namespace, exc)
            return
        for item in items:
            uid = item.metadata.uid or f"event-{item.metadata.name}-{namespace}"
            e = K8sEvent(
                uid=uid, name=item.metadata.name, namespace=namespace,
                labels=item.metadata.labels or {}, annotations=item.metadata.annotations or {},
                created_at=_ts(item.metadata.creation_timestamp),
                reason=item.reason or "", message=item.message or "",
                event_type=item.type or "Normal",
                involved_kind=item.involved_object.kind or "",
                involved_name=item.involved_object.name or "",
                count=item.count or 1,
                first_time=_ts(item.first_timestamp),
                last_time=_ts(item.last_timestamp),
                raw=item.to_dict(),
            )
            graph.add_entity(e)
            involved = self._find_by_name(graph, e.involved_name, namespace, e.involved_kind)
            if involved:
                graph.add_edge(Edge(involved.uid, e.uid, RelationshipType.HAS_EVENT))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _labels_match(selector: dict[str, str], labels: dict[str, str]) -> bool:
        return all(labels.get(k) == v for k, v in selector.items())

    @staticmethod
    def _find_by_name(graph: OntologyGraph, name: str, namespace: str | None, kind: str):
        if not name:
            return None
        for entity in graph.entities():
            entity_kind = (
                entity.kind.value
                if hasattr(entity.kind, "value")
                else str(entity.kind)
            )
            if (entity_kind == kind and entity.name == name
                    and (namespace is None or entity.namespace == namespace)):
                return entity
        return None

    def _lazy_edge(
        self, graph: OntologyGraph,
        source_uid: str, namespace: str,
        target_name: str, target_kind: str,
        rel_type: RelationshipType,
    ) -> None:
        target = self._find_by_name(graph, target_name, namespace, target_kind)
        if target:
            graph.add_edge(Edge(source_uid, target.uid, rel_type))
