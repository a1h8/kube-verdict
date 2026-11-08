"""CONFIGURES (ontology/relations.py), on fixtures written for this test, not S2's."""
from __future__ import annotations

from ontology.entities import ConfigMap, Namespace, Pod
from ontology.graph import OntologyGraph
from ontology.relations import build_configures
from ontology.relationships import RelationshipType


def _graph(container: dict, pod_ns: str = "ns") -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns", name="ns"))
    g.add_entity(ConfigMap(uid="cm", name="settings", namespace="ns"))
    g.add_entity(Pod(uid="p", name="app-1", namespace=pod_ns, raw={"spec": {"containers": [container]}}))
    build_configures(g)
    return g


def _edges(g: OntologyGraph) -> list:
    return [e for e in g._edges if e.rel_type == RelationshipType.CONFIGURES]


def test_env_from_links_the_configmap_to_the_pod():
    es = _edges(_graph({"name": "app", "env_from": [{"config_map_ref": {"name": "settings"}}]}))
    assert [(e.source_uid, e.target_uid, e.metadata["method"]) for e in es] == [("cm", "p", "envFrom")]


def test_a_key_reference_links_it_too_in_api_json_form():
    es = _edges(_graph({"name": "app", "env": [{"name": "DB_HOST",
                                                 "valueFrom": {"configMapKeyRef": {"name": "settings", "key": "db"}}}]}))
    assert len(es) == 1 and es[0].metadata["method"] == "env DB_HOST from key db"


def test_a_configmap_of_another_namespace_is_not_linked():
    assert not _edges(_graph({"name": "app", "env_from": [{"config_map_ref": {"name": "settings"}}]}, pod_ns="other"))


def test_a_container_that_reads_no_configmap_is_not_linked():
    assert not _edges(_graph({"name": "app", "env": [{"name": "X", "value": "settings"}]}))
