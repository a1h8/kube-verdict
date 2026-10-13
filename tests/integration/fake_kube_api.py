"""
Serve a case's ``kube/`` fixtures to the real ``K8sCollector``, as real client objects.

Spec: docs/test-cases.md → *Live parity*. The fixture YAML documents are
deserialized into ``kubernetes.client`` models (``V1Pod``, ``V1Deployment``, …),
exactly what the API returns live, and served by fake ``CoreV1Api`` /
``AppsV1Api`` objects. ``K8sCollector.collect()`` then runs unchanged, so a
graph built this way is what production would build from the same cluster
state.
"""
from __future__ import annotations

import copy

from types import SimpleNamespace
from unittest.mock import MagicMock

from kubernetes.client import ApiClient, Configuration

from ingestion.k8s_collector import K8sCollector

# Hand-written fixtures omit fields a real API always fills (a container status's
# image, an event's source…). Client-side validation would reject them; the
# collector does not need them, so it is off for the fake API only.
_CONF = Configuration()
_CONF.client_side_validation = False
_API = ApiClient(configuration=_CONF)


def _complete(doc: dict) -> dict:
    """Fill the uids a real API always returns and hand-written fixtures often omit.

    Deterministic (derived from kind and name), and nothing else is touched.
    """
    doc = copy.deepcopy(doc)
    meta = doc.setdefault("metadata", {})
    meta.setdefault("uid", f"fixture-{doc.get('kind', 'obj')}-{meta.get('name', '')}".lower())
    for ref in meta.get("ownerReferences") or []:
        ref.setdefault("uid", f"fixture-{ref.get('kind', '')}-{ref.get('name', '')}".lower())
    return doc


def _models(docs: list[dict], model: str, namespace: str | None) -> list:
    out = []
    for doc in docs:
        doc_ns = (doc.get("metadata") or {}).get("namespace")
        if namespace is not None and doc_ns not in (None, namespace):
            continue
        out.append(_API._ApiClient__deserialize(_complete(doc), model))
    return out


def _lister(docs: list[dict], model: str):
    def list_(namespace: str | None = None, *args, **kwargs):
        return SimpleNamespace(items=_models(docs, model, namespace))
    return list_


def collector_for(observed: dict, namespace: str) -> K8sCollector:
    """A real K8sCollector whose API clients serve ``observed`` (case_loader's dict)."""
    c = K8sCollector.__new__(K8sCollector)
    c.kube_version = None
    c._api_client = _API

    core = MagicMock(name="CoreV1Api")
    core.list_namespace.return_value = SimpleNamespace(items=[_API._ApiClient__deserialize(
        {"metadata": {"name": namespace, "uid": f"ns-{namespace}"}, "status": {"phase": "Active"}},
        "V1Namespace")])
    core.list_node.return_value = SimpleNamespace(items=[])
    core.list_persistent_volume.return_value = SimpleNamespace(items=[])
    core.list_namespaced_pod.side_effect = _lister(observed.get("pods", []), "V1Pod")
    core.list_namespaced_config_map.side_effect = _lister(observed.get("configmaps", []), "V1ConfigMap")
    core.list_namespaced_persistent_volume_claim.side_effect = _lister(
        observed.get("pvcs", []), "V1PersistentVolumeClaim")
    core.list_namespaced_service_account.side_effect = _lister(
        observed.get("serviceaccounts", []), "V1ServiceAccount")
    core.list_namespaced_event.side_effect = _lister(observed.get("events", []), "CoreV1Event")
    core.list_namespaced_service.return_value = SimpleNamespace(items=[])

    apps = MagicMock(name="AppsV1Api")
    apps.list_namespaced_deployment.side_effect = _lister(observed.get("deployments", []), "V1Deployment")
    for name in ("list_namespaced_stateful_set", "list_namespaced_daemon_set",
                 "list_namespaced_replica_set"):
        getattr(apps, name).return_value = SimpleNamespace(items=[])

    c._core, c._apps, c._net = core, apps, MagicMock(name="NetworkingV1Api")
    c._ingress_lister = lambda graph, ns: None
    c._discovery = MagicMock(name="APIServerDiscovery")
    c._discovery.list_all_resources.return_value = []
    c._discovery.vocabulary_summary.return_value = ""
    return c
