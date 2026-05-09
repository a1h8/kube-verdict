from ontology.entities import Pod, Deployment, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType
from ontology.version import KubeVersion


def _make_pod(uid: str, namespace: str = "default") -> Pod:
    return Pod(uid=uid, name=uid, namespace=namespace,
               labels={"app": "test"}, phase="Running")


def _make_deploy(uid: str, namespace: str = "default") -> Deployment:
    return Deployment(uid=uid, name=uid, namespace=namespace,
                      replicas=3, ready_replicas=3)


class TestOntologyGraph:
    def test_add_and_get_entity(self):
        g = OntologyGraph()
        pod = _make_pod("p1")
        g.add_entity(pod)
        assert g.get("p1") is pod

    def test_get_missing_returns_none(self):
        g = OntologyGraph()
        assert g.get("nonexistent") is None

    def test_node_count(self):
        g = OntologyGraph()
        g.add_entity(_make_pod("p1"))
        g.add_entity(_make_pod("p2"))
        assert g.node_count == 2

    def test_edge_count(self):
        g = OntologyGraph()
        g.add_entity(_make_pod("p1"))
        g.add_entity(_make_deploy("d1"))
        g.add_edge(Edge("d1", "p1", RelationshipType.OWNS))
        assert g.edge_count == 1

    def test_entities_by_kind(self):
        g = OntologyGraph()
        g.add_entity(_make_pod("p1"))
        g.add_entity(_make_pod("p2"))
        g.add_entity(_make_deploy("d1"))
        pods = list(g.entities(ResourceKind.POD))
        assert len(pods) == 2
        deploys = list(g.entities(ResourceKind.DEPLOYMENT))
        assert len(deploys) == 1

    def test_entities_all(self):
        g = OntologyGraph()
        g.add_entity(_make_pod("p1"))
        g.add_entity(_make_deploy("d1"))
        assert g.node_count == 2

    def test_neighbors_outgoing(self):
        g = OntologyGraph()
        g.add_entity(_make_deploy("d1"))
        g.add_entity(_make_pod("p1"))
        g.add_edge(Edge("d1", "p1", RelationshipType.OWNS))
        neighbours = g.neighbors("d1")
        assert len(neighbours) == 1
        assert neighbours[0].uid == "p1"

    def test_neighbors_filtered_by_rel(self):
        g = OntologyGraph()
        g.add_entity(_make_deploy("d1"))
        g.add_entity(_make_pod("p1"))
        g.add_entity(_make_pod("p2"))
        g.add_edge(Edge("d1", "p1", RelationshipType.OWNS))
        g.add_edge(Edge("d1", "p2", RelationshipType.EXPOSES))
        owns = g.neighbors("d1", rel_type=RelationshipType.OWNS)
        assert len(owns) == 1 and owns[0].uid == "p1"

    def test_bfs_depth_1(self):
        g = OntologyGraph()
        d = _make_deploy("d1")
        p1 = _make_pod("p1")
        p2 = _make_pod("p2")
        g.add_entity(d)
        g.add_entity(p1)
        g.add_entity(p2)
        g.add_edge(Edge("d1", "p1", RelationshipType.OWNS))
        g.add_edge(Edge("p1", "p2", RelationshipType.OWNS))
        result = g.bfs("d1", max_depth=1)
        uids = {e.uid for e in result}
        assert "p1" in uids
        assert "p2" not in uids   # depth=1 stops before p2

    def test_bfs_depth_2(self):
        g = OntologyGraph()
        for uid in ("d1", "p1", "p2"):
            g.add_entity(_make_pod(uid))
        g.add_edge(Edge("d1", "p1", RelationshipType.OWNS))
        g.add_edge(Edge("p1", "p2", RelationshipType.OWNS))
        result = g.bfs("d1", max_depth=2)
        uids = {e.uid for e in result}
        assert {"p1", "p2"} == uids

    def test_bfs_no_cycles(self):
        g = OntologyGraph()
        for uid in ("a", "b", "c"):
            g.add_entity(_make_pod(uid))
        g.add_edge(Edge("a", "b", RelationshipType.OWNS))
        g.add_edge(Edge("b", "c", RelationshipType.OWNS))
        g.add_edge(Edge("c", "a", RelationshipType.OWNS))  # cycle
        result = g.bfs("a", max_depth=10)
        assert len(result) == 2  # b and c, no infinite loop

    def test_summary_includes_version(self):
        g = OntologyGraph(server_version=KubeVersion(1, 28, "v1.28.3+k3s1"))
        g.add_entity(_make_pod("p1"))
        summary = g.summary()
        assert "v1.28.3+k3s1" in summary
        assert "Pod" in summary

    def test_subgraph_for_incident(self, synthetic_graph):
        seeds = ["pod-api-xyz"]
        result = synthetic_graph.subgraph_for_incident(seeds, depth=1)
        uids = {e.uid for e in result}
        # Should include at least node, configmap, secret, pvc, helm release
        assert len(uids) >= 3
