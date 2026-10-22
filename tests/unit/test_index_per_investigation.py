"""Each investigation indexes the cluster it collected, with the knowledge base, and keeps
nothing between runs — docs/fix-plan-001.md, H40 (an API store from an earlier run put its
pods in the prompt) and H39 (examples and documents were indexed by the UI only)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ontology.entities import Pod
from ontology.graph import OntologyGraph
from tests.conftest import FakeEmbedder
from workflow.nodes import _get_infra, ingest_node, index_node

ROOT = Path(__file__).resolve().parents[2]


def _graph(pod_name):
    g = OntologyGraph()
    g.add_entity(Pod(uid=pod_name, name=pod_name, namespace="prod", phase="Running"))
    return g


def _run(graph, examples=0):
    k8s = MagicMock(); k8s.collect.return_value = graph; k8s.kube_version = None
    pol = MagicMock(last_error=None)
    pol.collect.return_value = MagicMock(fail_count=0, audit_count=0, mutation_webhooks=0)
    ex_indexer = MagicMock(); ex_indexer.index_all.return_value = examples
    with patch("ingestion.K8sCollector", return_value=k8s), \
            patch("ingestion.HelmCollector", return_value=MagicMock()), \
            patch("ingestion.HelmDriftDetector", return_value=MagicMock(detect_all=MagicMock(return_value=0))), \
            patch("ingestion.PolicyCollector", return_value=pol), \
            patch("workflow.nodes.Embedder", FakeEmbedder), \
            patch("knowledge.ExampleIndexer", return_value=ex_indexer) as ex_cls, \
            patch("knowledge.DocIndexer") as doc_cls, \
            patch("vectorstore.store.FAISSStore.save") as save:
        doc_cls.return_value.index_all.return_value = 0
        cfg = {"configurable": {"thread_id": "session-1"}}
        state = ingest_node({"namespaces": ["prod"]}, cfg)
        index_node(state, cfg)
        store = _get_infra(cfg, state)[1]
    return store, ex_cls, doc_cls, save


def _texts(store):
    return " ".join(m.get("text", "") for m in store._metadata)


def test_the_second_investigation_does_not_see_the_first_cluster():
    first, *_ = _run(_graph("old-evicted-pod"))
    second, *_ = _run(_graph("api-healthy"))
    assert "old-evicted-pod" in _texts(first)                          # control
    assert "old-evicted-pod" not in _texts(second) and "api-healthy" in _texts(second)


def test_the_knowledge_base_is_indexed_and_nothing_is_saved():
    _, ex_cls, doc_cls, save = _run(_graph("api"), examples=2)
    ex_cls.return_value.index_all.assert_called_once()
    doc_cls.return_value.index_all.assert_called_once()
    save.assert_not_called()


def test_the_api_loads_no_store_and_passes_none():
    app_src = (ROOT / "api/app.py").read_text()
    assert "faiss_store" not in app_src and "rebuild_from_db" not in app_src
    for path in ("api/routes/sessions.py", "api/routes/investigate.py"):
        assert "_faiss_store" not in (ROOT / path).read_text()
