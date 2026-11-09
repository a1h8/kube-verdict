"""The generic primitives of ontology/declared_relations.py, on fixtures written for this test."""
from __future__ import annotations

from ontology.declared_relations import _field_equals, _label_selector, resolve
from ontology.entities import Pod, Service
from ontology.graph import OntologyGraph
from ontology.spec_loader import RelationSpec


def test_a_path_reads_both_spellings_and_walks_lists():
    camel = {"spec": {"containers": [{"envFrom": [{"configMapRef": {"name": "a"}}, {"configMapRef": {"name": "b"}}]}]}}
    snake = {"spec": {"containers": [{"env_from": [{"config_map_ref": {"name": "a"}}]}]}}
    assert resolve(camel, "spec.containers[].envFrom[].configMapRef.name") == ["a", "b"]
    assert resolve(snake, "spec.containers[].envFrom[].configMapRef.name") == ["a"]
    assert resolve({}, "spec.containers[].name") == []


def test_a_list_item_is_selected_by_a_key():
    class E:
        raw = {"spec": {"limits": [{"type": "Pod", "max": {"memory": "2Gi"}},
                                   {"type": "Container", "max": {"memory": "1Gi"}}]}}
    assert _field_equals(E(), "spec.limits[type=Container].max.memory", "1Gi")
    assert not _field_equals(E(), "spec.limits[type=Container].max.memory", "2Gi")


def test_a_label_selector_matches_same_namespace_pods_with_every_label():
    g = OntologyGraph()
    g.add_entity(Service(uid="s", name="web", namespace="ns", raw={"spec": {"selector": {"app": "web", "tier": "fe"}}}))
    g.add_entity(Pod(uid="p1", name="web-1", namespace="ns", labels={"app": "web", "tier": "fe", "x": "y"}))
    g.add_entity(Pod(uid="p2", name="web-2", namespace="ns", labels={"app": "web"}))                 # one label short
    g.add_entity(Pod(uid="p3", name="web-3", namespace="other", labels={"app": "web", "tier": "fe"}))  # other ns
    spec = RelationSpec("EXPOSES", "Service", "Pod", "label_selector", {"selector": "spec.selector"}, ("https://x",))
    assert [(s.uid, d.uid) for s, d, *_ in _label_selector(g, spec)] == [("s", "p1")]
