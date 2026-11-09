"""OWNS from ownerReferences and QUOTA_BLOCKS (ontology/relations.py), on fixtures written for this test."""
from __future__ import annotations

from ontology.discovery import APIResourceInfo
from ontology.dynamic_entity import GenericEntity
from ontology.entities import K8sEvent, Namespace, Pod, ReplicaSet
from ontology.graph import OntologyGraph
from ontology.declared_relations import build_named
from ontology.relationships import RelationshipType


def build_owns(g):
    return build_named(g, "OWNS")


def build_quota_blocks(g):
    return build_named(g, "QUOTA_BLOCKS")


def _res(kind: str, plural: str, group: str = "", version: str = "v1") -> APIResourceInfo:
    return APIResourceInfo(group=group, version=version, kind=kind, plural=plural, namespaced=True, verbs=["list"])


def _generic(kind: str, plural: str, name: str, owner: tuple[str, str] | None = None, group: str = "") -> GenericEntity:
    meta = {"name": name, "namespace": "ns", "uid": f"{kind}-{name}"}
    if owner:
        meta["ownerReferences"] = [{"kind": owner[0], "name": owner[1]}]
    return GenericEntity.from_api_object({"metadata": meta, "spec": {}}, _res(kind, plural, group))


def _rels(g: OntologyGraph, rel) -> set[tuple[str, str]]:
    return {(e.source_uid, e.target_uid) for e in g._edges if e.rel_type == rel}


def test_a_cronjob_owns_its_jobs_and_a_job_its_pods():
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns", name="ns"))
    g.add_entity(_generic("CronJob", "cronjobs", "export", group="batch"))
    g.add_entity(_generic("Job", "jobs", "export-1", owner=("CronJob", "export"), group="batch"))
    g.add_entity(Pod(uid="p", name="export-1-a", namespace="ns", owner_ref_kind="Job", owner_ref_name="export-1"))
    build_owns(g)
    assert _rels(g, RelationshipType.OWNS) == {("CronJob-export", "Job-export-1"), ("Job-export-1", "p")}


def test_an_owner_in_another_namespace_or_absent_is_not_linked():
    g = OntologyGraph()
    g.add_entity(Pod(uid="p", name="x", namespace="ns", owner_ref_kind="Job", owner_ref_name="gone"))
    build_owns(g)
    assert not _rels(g, RelationshipType.OWNS)


def test_an_edge_already_built_is_not_doubled():
    g = OntologyGraph()
    g.add_entity(ReplicaSet(uid="rs", name="web-1", namespace="ns"))
    g.add_entity(Pod(uid="p", name="web-1-a", namespace="ns", owner_ref_kind="ReplicaSet", owner_ref_name="web-1"))
    from ontology.relationships import Edge
    g.add_edge(Edge("rs", "p", RelationshipType.OWNS))
    build_owns(g)
    assert sum(1 for e in g._edges if e.rel_type == RelationshipType.OWNS) == 1


def _quota_graph(message: str, with_rs: bool) -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns", name="ns"))
    g.add_entity(_generic("ResourceQuota", "resourcequotas", "team-quota"))
    if with_rs:
        g.add_entity(ReplicaSet(uid="rs", name="api-1", namespace="ns"))
    g.add_entity(K8sEvent(uid="ev", name="api-1.1", namespace="ns", reason="FailedCreate", event_type="Warning",
                          involved_kind="ReplicaSet", involved_name="api-1", message=message))
    build_quota_blocks(g)
    return g


_REFUSED = ('Error creating: pods "api-1-x" is forbidden: exceeded quota: team-quota, requested: pods=1, '
            'used: pods=10, limited: pods=10')


def test_a_quota_refusal_links_the_quota_to_the_event_and_the_refused_object():
    g = _quota_graph(_REFUSED, with_rs=True)
    assert _rels(g, RelationshipType.QUOTA_BLOCKS) == {("ResourceQuota-team-quota", "ev"),
                                                       ("ResourceQuota-team-quota", "rs")}


def test_without_the_refused_object_the_event_is_still_linked():
    assert _rels(_quota_graph(_REFUSED, with_rs=False), RelationshipType.QUOTA_BLOCKS) == {
        ("ResourceQuota-team-quota", "ev")}


def test_a_refusal_naming_another_quota_is_not_linked():
    g = _quota_graph(_REFUSED.replace("team-quota", "other-quota"), with_rs=True)
    assert not _rels(g, RelationshipType.QUOTA_BLOCKS)


def test_an_owner_of_the_same_name_in_another_namespace_is_not_linked():
    g = OntologyGraph()
    other = _generic("Job", "jobs", "export-1", group="batch")
    other.namespace = "elsewhere"
    g.add_entity(other)
    g.add_entity(Pod(uid="p", name="export-1-a", namespace="ns", owner_ref_kind="Job", owner_ref_name="export-1"))
    build_owns(g)
    assert not _rels(g, RelationshipType.OWNS)
