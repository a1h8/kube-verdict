"""LIMITS (ontology/relations.py), on fixtures written for this test, not S2's (docs/correlation-engine.md § 1)."""
from __future__ import annotations

from ontology.discovery import APIResourceInfo
from ontology.dynamic_entity import GenericEntity
from ontology.entities import Deployment, K8sEvent, Namespace, Pod, ReplicaSet
from ontology.graph import OntologyGraph
from ontology.relations import build_limits
from ontology.relationships import RelationshipType

_LR = APIResourceInfo(group="", version="v1", kind="LimitRange", plural="limitranges", namespaced=True,
                      verbs=["list"])


def _limitrange(default_mem="256Mi", max_mem="1Gi") -> GenericEntity:
    return GenericEntity.from_api_object({"metadata": {"name": "defaults", "namespace": "ns", "uid": "lr"},
                                          "spec": {"limits": [{"type": "Container", "default": {"memory": default_mem},
                                                               "max": {"memory": max_mem}}]}}, _LR)


def _workload(g: OntologyGraph, name: str, declared: dict | None) -> None:
    res = {"limits": declared} if declared else {}
    g.add_entity(Deployment(uid=f"d-{name}", name=name, namespace="ns", raw={"spec": {"template": {"spec": {
        "containers": [{"name": name, "resources": res}]}}}}))
    g.add_entity(ReplicaSet(uid=f"rs-{name}", name=f"{name}-1", namespace="ns", raw={
        "metadata": {"owner_references": [{"kind": "Deployment", "name": name}]}, "spec": {}}))


def _pod(g: OntologyGraph, name: str, limit: str, note: str = "", owner: str | None = None) -> Pod:
    p = Pod(uid=f"p-{name}", name=f"{name}-1-a", namespace="ns", owner_ref_kind="ReplicaSet" if owner else "",
            owner_ref_name=f"{owner}-1" if owner else "",
            k8s_annotations={"kubernetes.io/limit-ranger": note} if note else {},
            raw={"spec": {"containers": [{"name": name, "resources": {"limits": {"memory": limit}}}]}})
    g.add_entity(p)
    return p


def _graph() -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns", name="ns"))
    g.add_entity(_limitrange())
    return g


def _limits(g: OntologyGraph) -> dict[str, dict]:
    return {e.target_uid: e.metadata for e in g._edges if e.rel_type == RelationshipType.LIMITS}


def test_an_injected_default_with_limitranger_s_record_is_linked_with_full_confidence():
    g = _graph()
    _workload(g, "worker", None)
    _pod(g, "worker", "256Mi", "LimitRanger plugin set: memory limit for container worker", owner="worker")
    build_limits(g)
    assert _limits(g)["p-worker"]["confidence"] == 1.0


def test_a_declared_limit_equal_to_the_default_is_not_linked():
    g = _graph()
    _workload(g, "api", {"memory": "256Mi"})           # the template declares it: LimitRanger set nothing
    _pod(g, "api", "256Mi", owner="api")
    build_limits(g)
    assert "p-api" not in _limits(g)


def test_an_equal_value_with_no_known_template_proves_nothing():
    g = _graph()
    _pod(g, "orphan", "256Mi")                          # no owner in the graph, no record
    build_limits(g)
    assert "p-orphan" not in _limits(g)


def test_the_record_alone_is_linked_with_lower_confidence():
    g = _graph()
    _pod(g, "orphan", "300Mi", "LimitRanger plugin set: memory limit for container orphan")
    build_limits(g)
    assert _limits(g)["p-orphan"]["confidence"] == 0.7


def test_a_refusal_links_the_limitrange_whose_max_it_quotes():
    g = _graph()
    g.add_entity(ReplicaSet(uid="rs-x", name="x-1", namespace="ns"))
    g.add_entity(K8sEvent(uid="ev", name="x-1.1", namespace="ns", reason="FailedCreate", event_type="Warning",
                          involved_kind="ReplicaSet", involved_name="x-1",
                          message='Error creating: pods "x-1-a" is forbidden: maximum memory usage per Container is '
                                  '1Gi, but limit is 2Gi'))
    build_limits(g)
    assert _limits(g)["rs-x"]["method"] == "admission refusal (max)"


def test_a_refusal_quoting_another_max_is_not_linked():
    g = _graph()
    g.add_entity(ReplicaSet(uid="rs-x", name="x-1", namespace="ns"))
    g.add_entity(K8sEvent(uid="ev", name="x-1.1", namespace="ns", reason="FailedCreate", event_type="Warning",
                          involved_kind="ReplicaSet", involved_name="x-1",
                          message='pods "x-1-a" is forbidden: maximum memory usage per Container is 4Gi, but limit is 8Gi'))
    build_limits(g)
    assert "rs-x" not in _limits(g)
