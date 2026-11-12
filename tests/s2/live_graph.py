"""S2's graph, built by the product's own collectors (docs/s2-correlation-cases.md, step 0, option B).

``K8sCollector`` and the policy collector run unchanged; a fake API serves them the case's ``kube/``
fixtures as ``kubernetes.client`` models, over every namespace the case holds. The rest is the test
loader's own steps, in its order: the Helm release, the alert / log / trace fixtures through their real
collectors, the applied-vs-live comparison, Helm drift. S1 keeps ``case_loader.build_graph``.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import yaml

from ingestion.helm_collector import belongs_to_release
from ingestion.helm_drift import HelmDriftDetector
from ingestion.k8s_collector import K8sCollector
from ingestion.policy_collector import PolicyCollector
from ontology.graph import Edge, OntologyGraph
from ontology.relationships import RelationshipType
from tests.integration.cases.case_loader import (_build_helm_release, _wire_loki_logs, _wire_otel_traces,
                                                 _wire_prometheus_alerts, load_case)
from ontology.discovery import APIServerDiscovery
from tests.integration.fake_kube_api import _API, _complete, _lister, _models

# what the fake API serves, and as which client model
_SERVED = {"namespace": "V1Namespace", "node": "V1Node", "pod": "V1Pod", "configmap": "V1ConfigMap",
           "persistentvolumeclaim": "V1PersistentVolumeClaim", "serviceaccount": "V1ServiceAccount",
           "event": "CoreV1Event", "service": "V1Service", "deployment": "V1Deployment",
           "statefulset": "V1StatefulSet", "daemonset": "V1DaemonSet", "replicaset": "V1ReplicaSet",
           "networkpolicy": "V1NetworkPolicy", "mutatingwebhookconfiguration": "V1MutatingWebhookConfiguration"}


# kinds a real API server serves cluster-scoped (kubectl api-resources --namespaced=false)
_CLUSTER_SCOPED = {"Namespace", "Node", "PersistentVolume", "StorageClass", "APIService",
                   "MutatingWebhookConfiguration", "ValidatingWebhookConfiguration", "ClusterIssuer", "ClusterRole",
                   "ClusterRoleBinding", "PriorityClass", "CustomResourceDefinition"}


def _plural(kind: str) -> str:
    low = kind.lower()
    if low.endswith("s"):
        return low + "es" if not low.endswith("points") else low
    if low.endswith("y"):
        return low[:-1] + "ies"
    return low + "s"


def discovery_for_case(docs: dict[str, list[dict]]) -> APIServerDiscovery:
    """The product's own ``APIServerDiscovery``, whose HTTP calls are answered from the case's objects.

    Only the API server is faked: its discovery documents (/api/v1, /apis, /apis/<group>/<version>) list each kind
    the case holds, with its group, version, plural and scope, and its list endpoints return the case's objects.
    Discovery, its skip set and namespace filter, and ``GenericEntity.from_api_object`` run unchanged, as in
    production (``K8sCollector.collect`` step 2). Added 2026-11-08: S2's runs had an empty discovery, so every kind
    the product collects only this way (ResourceQuota, LimitRange, HPA, Job, CronJob, APIService, CRDs) was missing.
    """
    kinds: dict[tuple[str, str], dict] = {}          # (group, kind) -> resource
    items: dict[str, list[dict]] = {}                 # path prefix -> objects
    for objs in docs.values():
        for obj in objs:
            kind, api_version = str(obj.get("kind", "")), str(obj.get("apiVersion", "v1"))
            if not kind:
                continue
            group, _, version = api_version.rpartition("/")
            res = kinds.setdefault((group, kind), {"group": group, "version": version, "kind": kind,
                                                   "plural": _plural(kind), "namespaced": kind not in _CLUSTER_SCOPED})
            base = f"/apis/{group}/{version}" if group else f"/api/{version}"
            ns = (obj.get("metadata") or {}).get("namespace")
            item = _complete(obj)
            items.setdefault(f"{base}/{res['plural']}", []).append(item)
            if res["namespaced"] and ns:
                items.setdefault(f"{base}/namespaces/{ns}/{res['plural']}", []).append(item)

    def _resource(r: dict) -> dict:
        return {"name": r["plural"], "kind": r["kind"], "namespaced": r["namespaced"], "verbs": ["get", "list", "watch"]}

    core = MagicMock(name="CoreV1Api")
    core.get_api_resources.return_value = SimpleNamespace(resources=[
        SimpleNamespace(name=r["plural"], kind=r["kind"], namespaced=r["namespaced"], verbs=["get", "list", "watch"],
                        short_names=[], categories=[]) for r in kinds.values() if not r["group"]])
    groups = sorted({(r["group"], r["version"]) for r in kinds.values() if r["group"]})
    apis = MagicMock(name="ApisApi")
    apis.get_api_versions.return_value = SimpleNamespace(groups=[
        SimpleNamespace(name=g, preferred_version=SimpleNamespace(version=v)) for g, v in groups])

    def call_api(path, method, **_kwargs):
        for g, v in groups:
            if path == f"/apis/{g}/{v}":
                return {"resources": [_resource(r) for r in kinds.values() if r["group"] == g]}
        return {"items": items.get(path, [])}

    client = MagicMock(name="ApiClient")
    client.call_api.side_effect = call_api
    d = APIServerDiscovery.__new__(APIServerDiscovery)
    d._client, d._core_api, d._apis_api, d.failures = client, core, apis, []
    return d


def kube_docs(case_dir: Path) -> dict[str, list[dict]]:
    """Every object of the case's kube/ files, by lower-case kind (List kinds unpacked)."""
    out: dict[str, list[dict]] = {}
    for path in sorted((case_dir / "kube").rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict):
                continue
            items = doc.get("items", []) if str(doc.get("kind", "")).endswith("List") else [doc]
            for item in items:
                out.setdefault(str(item.get("kind", "")).lower(), []).append(item)
    return out


def namespaces_of(docs: dict[str, list[dict]]) -> list[str]:
    names = {(d.get("metadata") or {}).get("namespace") for kind in docs.values() for d in kind}
    names |= {(d.get("metadata") or {}).get("name") for d in docs.get("namespace", [])}
    return sorted(n for n in names if n)


def collector_for_case(docs: dict[str, list[dict]], namespaces: list[str]) -> K8sCollector:
    """A real K8sCollector whose API clients serve the case's objects, in every namespace."""
    c = K8sCollector.__new__(K8sCollector)
    c.kube_version = None
    c._api_client = _API
    listed = {d["metadata"]["name"] for d in docs.get("namespace", [])}
    # a real API always returns a Namespace's phase; hand-written fixtures may omit it
    namespaces_docs = [{**d, "status": d.get("status") or {"phase": "Active"}} for d in docs.get("namespace", [])] + [
        {"metadata": {"name": n, "uid": f"ns-{n}"}, "status": {"phase": "Active"}} for n in namespaces if n not in listed]

    core = MagicMock(name="CoreV1Api")
    core.list_namespace.return_value = SimpleNamespace(items=_models(namespaces_docs, "V1Namespace", None))
    core.list_node.return_value = SimpleNamespace(items=_models(docs.get("node", []), "V1Node", None))
    core.list_persistent_volume.return_value = SimpleNamespace(items=[])
    for method, kind in (("list_namespaced_pod", "pod"), ("list_namespaced_config_map", "configmap"),
                         ("list_namespaced_persistent_volume_claim", "persistentvolumeclaim"),
                         ("list_namespaced_service_account", "serviceaccount"), ("list_namespaced_event", "event"),
                         ("list_namespaced_service", "service")):
        getattr(core, method).side_effect = _lister(docs.get(kind, []), _SERVED[kind])
    apps = MagicMock(name="AppsV1Api")
    for method, kind in (("list_namespaced_deployment", "deployment"), ("list_namespaced_stateful_set", "statefulset"),
                         ("list_namespaced_daemon_set", "daemonset"), ("list_namespaced_replica_set", "replicaset")):
        getattr(apps, method).side_effect = _lister(docs.get(kind, []), _SERVED[kind])
    netpol = MagicMock(name="NetworkingV1Api")
    netpol.list_namespaced_network_policy.side_effect = _lister(docs.get("networkpolicy", []), _SERVED["networkpolicy"])

    c._core, c._apps, c._net, c._netpol_api = core, apps, netpol, netpol
    c._ingress_lister = lambda graph, ns: None
    c._discovery = discovery_for_case(docs)
    return c


def _add_webhooks(graph: OntologyGraph, docs: dict[str, list[dict]]) -> int:
    """The policy collector's own webhook reader, served the case's MutatingWebhookConfigurations."""
    pc = PolicyCollector.__new__(PolicyCollector)
    pc._timeout = 10
    pc._admission = MagicMock(name="AdmissionregistrationV1Api")
    pc._admission.list_mutating_webhook_configuration.return_value = SimpleNamespace(
        items=_models(docs.get("mutatingwebhookconfiguration", []), _SERVED["mutatingwebhookconfiguration"], None))
    return pc._collect_webhooks(graph)


def build_graph_live(case_dir: Path, wire_signals: bool = True) -> OntologyGraph:
    """``wire_signals=False`` leaves the alert, log and trace fixtures to the workflow's own collector
    nodes, as ``case_loader.build_graph`` does for the replay."""
    case = load_case(case_dir)
    docs = kube_docs(case_dir)
    namespaces = namespaces_of(docs)
    collector = collector_for_case(docs, namespaces)
    graph = collector.collect(namespaces=namespaces)
    _add_webhooks(graph, docs)
    from ontology.declared_relations import build_declared
    build_declared(graph)        # as the workflow does once the policy collector has run

    ns = case["expect"].get("namespace", "default")
    release = _build_helm_release(name=case["expect"].get("release", case["case_name"]), namespace=ns,
                                  declared_values=case["helm_values"], helmfile=case["helmfile"])
    graph.add_entity(release)
    for entity in list(graph.entities()):
        if entity.kind.value in ("Deployment", "StatefulSet", "DaemonSet", "Pod") and belongs_to_release(entity, release.name):
            graph.add_edge(Edge(entity.uid, release.uid, RelationshipType.MANAGED_BY_HELM))

    if wire_signals:
        _wire_otel_traces(graph, case.get("otel_traces", []))
        _wire_prometheus_alerts(graph, case.get("prometheus_alerts", []))
        _wire_loki_logs(graph, case.get("loki_streams", []), case["expect"].get("query", ""))

    applied_path = case_dir / "helm" / "manifest.yaml"
    if applied_path.exists():
        from ingestion.manifest_differ import compare_applied_to_live
        release.applied_manifest = [d for d in yaml.safe_load_all(applied_path.read_text())
                                    if isinstance(d, dict) and d.get("kind")]
        compare_applied_to_live(graph, covered=collector.covered)
    HelmDriftDetector().detect_all(graph)
    return graph
