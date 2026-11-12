"""docs/s2g/gitops-cases.md, change 2b: docs/correlation-engine.md § 4 point 4 (the cause is the farthest abnormal object
of the chain) and point 5 (chains ranked by score, then length, then fqn). Fresh fixtures; the anchors are replaced by
a stub evaluator so that the walk alone is tested."""
import pytest

import rca.correlation as c
from ontology.discovery import APIResourceInfo
from ontology.dynamic_entity import GenericEntity
from ontology.entities import Pod
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType as R


def _generic(kind, name, uid, group="example.test"):
    info = APIResourceInfo(group=group, version="v1", kind=kind, plural=kind.lower() + "s", namespaced=True,
                           verbs=["list"])
    return GenericEntity.from_api_object({"apiVersion": f"{group}/v1", "metadata": {
        "name": name, "namespace": "ns", "uid": uid}}, info)


def _graph(conf=1.0):
    g = OntologyGraph()
    pod = Pod(uid="p", name="w", namespace="ns")
    x = _generic("AAAConfig", "x", "x")              # sorts before "Application"
    app = _generic("Application", "app", "app", "argoproj.io")
    for e in (pod, x, app):
        g.add_entity(e)
    g.add_edge(Edge("x", "p", R.CONFIGURES, {"evidence": "env", "confidence": conf}))
    g.add_edge(Edge("app", "x", R.MANAGES, {"evidence": "inventory", "confidence": conf}))
    return g, pod


@pytest.fixture
def abnormal(monkeypatch):
    bad = {"x": "x is stale", "app": "app's sync failed"}

    def fake(graph, node, edge, came_from):
        return (bad[node.uid], node) if node.uid in bad else None
    monkeypatch.setattr(c, "evaluate", fake)
    return bad


def test_the_farthest_abnormal_object_is_the_cause(abnormal):
    g, pod = _graph()
    chains = c.correlate(g, [pod])
    assert [ch.cause for ch in chains] == ["Application/ns/app"]


def test_a_nearer_abnormal_object_stays_as_a_hop_with_its_mechanism(abnormal):
    g, pod = _graph()
    (chain,) = c.correlate(g, [pod])
    assert [h.src for h in chain.hops] == ["AAAConfig/ns/x", "Application/ns/app"]
    assert "x is stale" in chain.hops[0].evidence


def test_a_nearer_object_is_still_a_cause_when_nothing_abnormal_is_above_it(abnormal):
    abnormal.pop("app")
    g, pod = _graph()
    assert [ch.cause for ch in c.correlate(g, [pod])] == ["AAAConfig/ns/x"]


def test_chains_rank_by_score_from_edge_confidence_before_length():
    a = c.Chain("s", "B/ns/b", "m", [c.Hop("B/ns/b", "OWNS", "s", "e")], score=0.25)
    b = c.Chain("s", "A/ns/a", "m", [c.Hop("X", "OWNS", "s", "e"), c.Hop("A/ns/a", "OWNS", "X", "e")], score=0.5)
    assert [ch.cause for ch in c.rank([a, b])] == ["A/ns/a", "B/ns/b"]


def test_a_chain_score_is_half_the_product_of_its_hop_confidences(abnormal):
    g, pod = _graph(conf=0.8)
    (chain,) = c.correlate(g, [pod])
    assert chain.score == pytest.approx(0.5 * 0.8 * 0.8)
