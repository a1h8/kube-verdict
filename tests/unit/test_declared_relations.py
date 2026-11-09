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


def test_a_reference_reads_its_target_kind_on_the_holder():
    from ontology.declared_relations import _name_reference
    from ontology.entities import Deployment, StatefulSet
    g = OntologyGraph()
    hpa = Service(uid="h", name="api", namespace="ns", raw={"spec": {"scaleTargetRef": {"kind": "StatefulSet",
                                                                                       "name": "api"}}})
    hpa.kind = "HorizontalPodAutoscaler"
    g.add_entity(hpa)
    g.add_entity(Deployment(uid="d", name="api", namespace="ns"))       # same name, other kind: not the target
    g.add_entity(StatefulSet(uid="s", name="api", namespace="ns"))
    spec = RelationSpec("SCALES", "HorizontalPodAutoscaler", "*", "name_reference",
                        {"path": "spec.scaleTargetRef.name", "kind_path": "spec.scaleTargetRef.kind",
                         "holder": "source"}, ("https://x",))
    assert [(s.uid, d.uid) for s, d, *_ in _name_reference(g, spec)] == [("h", "s")]


def test_an_empty_pod_selector_selects_every_pod_of_its_namespace_only_when_declared_so():
    g = OntologyGraph()
    np_ = Service(uid="np", name="deny", namespace="ns", raw={"spec": {"podSelector": {}}})
    np_.kind = "NetworkPolicy"
    g.add_entity(np_)
    g.add_entity(Pod(uid="p1", name="a", namespace="ns", labels={"x": "1"}))
    g.add_entity(Pod(uid="p2", name="b", namespace="other", labels={"x": "1"}))
    base = {"selector": "spec.podSelector", "scope": "same_namespace"}
    all_ = RelationSpec("RESTRICTS", "NetworkPolicy", "Pod", "label_selector", {**base, "empty_selects_all": True},
                        ("https://x",))
    assert [(s.uid, d.uid) for s, d, *_ in _label_selector(g, all_)] == [("np", "p1")]
    none = RelationSpec("RESTRICTS", "NetworkPolicy", "Pod", "label_selector", base, ("https://x",))
    assert list(_label_selector(g, none)) == []


def test_a_cluster_scope_selector_matches_namespaces_by_labels():
    from ontology.entities import Namespace
    g = OntologyGraph()
    wh = Service(uid="w", name="inj", namespace=None,
                 raw={"webhooks": [{"namespace_selector": {"match_labels": {"mesh": "on"}, "match_expressions": None}}]})
    wh.kind = "MutatingWebhook"
    g.add_entity(wh)
    g.add_entity(Namespace(uid="n1", name="shop", labels={"mesh": "on"}))
    g.add_entity(Namespace(uid="n2", name="pay", labels={"mesh": "off"}))
    spec = RelationSpec("ADMITS", "MutatingWebhook", "Namespace", "label_selector",
                        {"selector": "webhooks[].namespaceSelector", "scope": "cluster"}, ("https://x",))
    assert [(s.uid, d.uid) for s, d, *_ in _label_selector(g, spec)] == [("w", "n1")]
