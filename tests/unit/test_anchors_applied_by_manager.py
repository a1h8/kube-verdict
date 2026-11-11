"""Change 1 of docs/s2g/gitops-cases.md: a manager whose field manager writes the limit that OOMKills its managed
object's pods. Fixtures written for this test, not S2-GitOps'."""
from ontology.anchors import evaluate
from ontology.discovery import APIResourceInfo
from ontology.dynamic_entity import GenericEntity
from ontology.entities import Deployment, Pod, ReplicaSet
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType as R

KEY = 'k:{"name":"w"}'


def _app(kind="Application", group="argoproj.io"):
    info = APIResourceInfo(group=group, version="v1alpha1", kind=kind, plural=kind.lower() + "s", namespaced=True,
                           verbs=["list"])
    return GenericEntity.from_api_object({"apiVersion": f"{group}/v1alpha1", "metadata": {
        "name": "billing", "namespace": "argocd", "uid": "app"}, "status": {}}, info)


def _graph(manager="argocd-controller", oom=True, field="f:memory"):
    g = OntologyGraph()
    mf = [{"manager": manager, "operation": "Apply", "time": "2026-11-02T09:02:00Z", "fieldsType": "FieldsV1",
           "fieldsV1": {"f:spec": {"f:template": {"f:spec": {"f:containers": {KEY: {"f:resources": {"f:limits": {
               field: {}}}}}}}}}}]
    dep = Deployment(uid="d", name="w", namespace="billing", raw={
        "api_version": "apps/v1", "metadata": {"managed_fields": mf},
        "spec": {"template": {"spec": {"containers": [{"name": "w", "resources": {"limits": {"memory": "256Mi"}}}]}}}})
    rs = ReplicaSet(uid="rs", name="w-1", namespace="billing")
    last = {"terminated": {"reason": "OOMKilled" if oom else "Error"}}
    pod = Pod(uid="p", name="w-1-a", namespace="billing", raw={"status": {"containerStatuses": [
        {"name": "w", "ready": False, "lastState": last}]}})
    app = _app()
    for e in (dep, rs, pod, app):
        g.add_entity(e)
    g.add_edge(Edge("d", "rs", R.OWNS, {}))
    g.add_edge(Edge("rs", "p", R.OWNS, {}))
    g.add_edge(Edge("app", "d", R.MANAGES, {"method": "inventory"}))
    return g, app, dep


def _eval(g, app, dep, rel=R.MANAGES):
    return evaluate(g, app, Edge("app", "d", rel, {"method": "inventory"}), dep)


def test_fires_when_the_managers_field_manager_owns_the_oomkilling_limit():
    g, app, dep = _graph()
    found = _eval(g, app, dep)
    assert found and found[1] is app and "written by argocd-controller" in found[0] and "1 of its pods" in found[0]


def test_not_when_another_manager_owns_it():
    g, app, dep = _graph(manager="kubectl")
    assert _eval(g, app, dep) is None


def test_not_without_an_oomkill():
    g, app, dep = _graph(oom=False)
    assert _eval(g, app, dep) is None


def test_not_when_the_manager_owns_another_field():
    g, app, dep = _graph(field="f:cpu")
    assert _eval(g, app, dep) is None


def test_not_over_another_relation():
    g, app, dep = _graph()
    assert _eval(g, app, dep, rel=R.OWNS) is None


def test_not_for_another_kind_of_manager():
    g, _, dep = _graph()
    other = _app(kind="Kustomization", group="kustomize.toolkit.fluxcd.io")
    g.add_entity(other)
    assert evaluate(g, other, Edge(other.uid, "d", R.MANAGES, {}), dep) is None
