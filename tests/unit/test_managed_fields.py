"""managedFields: who owns each field, and what the applied-vs-live comparison does with it (M2 step 2).
Fixtures written for this test, not S2's (S2's fixtures hold no fieldsV1)."""
from __future__ import annotations

from ingestion.manifest_differ import ManifestDiffer
from ontology.entities import Deployment, Namespace
from ontology.graph import OntologyGraph
from ontology.managed_fields import container_key, fields_path, owners

_API = container_key("api")


def _deploy() -> Deployment:
    mf = [
        {"manager": "helm", "operation": "Update", "time": "2026-01-01T08:00:00Z", "fieldsType": "FieldsV1",
         "fieldsV1": {"f:spec": {"f:template": {"f:spec": {"f:containers": {_API: {".": {}, "f:image": {},
                                                                                  "f:resources": {"f:limits": {"f:memory": {}}}}}}}}}},
        {"manager": "kube-controller-manager", "operation": "Update", "subresource": "scale",
         "time": "2026-01-01T09:00:00Z", "fieldsType": "FieldsV1", "fieldsV1": {"f:spec": {"f:replicas": {}}}},
        {"manager": "kubectl", "operation": "Update", "time": "2026-01-01T10:42:00Z", "fieldsType": "FieldsV1",
         "fieldsV1": {"f:spec": {"f:template": {"f:spec": {"f:containers": {_API: {
             "f:resources": {"f:requests": {"f:cpu": {}}}}}}}}}},
    ]
    return Deployment(uid="d", name="api", namespace="ns", replicas=10,
                      containers=[{"name": "api", "image": "api:1", "resources": {"requests": {"cpu": "100m"},
                                                                                   "limits": {"memory": "1Gi"}}}],
                      raw={"metadata": {"managedFields": mf}})


def test_each_field_has_its_owner():
    o = owners(_deploy())
    assert o[("f:spec", "f:replicas")].manager == "kube-controller-manager"
    assert o[fields_path("Deployment", "container.api.resources.requests.cpu")].manager == "kubectl"
    assert o[fields_path("Deployment", "container.api.image")].manager == "helm"
    assert o[("f:spec", "f:template", "f:spec", "f:containers", _API)].manager == "helm"    # '.' is the item itself


def _applied() -> list[dict]:
    return [{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "api", "namespace": "ns"},
             "spec": {"replicas": 3, "template": {"spec": {"containers": [
                 {"name": "api", "image": "api:1", "resources": {"requests": {"cpu": "500m"}, "limits": {"memory": "1Gi"}}}]}}}}]


def _diff():
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns", name="ns"))
    d = _deploy()
    g.add_entity(d)
    drifts = ManifestDiffer(track_orphans=False).diff(_applied(), g, namespace="ns", prefix="applied")
    return {x.field_path for x in drifts}, d.annotations


def test_a_field_a_controller_owns_is_not_a_drift():
    paths, ann = _diff()
    assert "spec.replicas" not in paths
    assert ann["applied.controller-owned.spec.replicas"].startswith("manager=kube-controller-manager")


def test_a_hand_change_is_a_drift_with_its_author_and_time():
    paths, ann = _diff()
    assert "container.api.resources.requests.cpu" in paths
    assert ann["applied.container.api.resources.requests.cpu.author"] == (
        "manager=kubectl operation=Update role=person entry_time=2026-01-01T10:42:00Z")
