import tempfile
from pathlib import Path

import pytest

from ontology.entities import Pod, Deployment, Service
from ontology.graph import OntologyGraph
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore


@pytest.fixture
def small_graph():
    g = OntologyGraph()
    g.add_entity(Pod(uid="pod-1", name="api-xyz", namespace="prod",
                     phase="Failed", restart_count=5))
    g.add_entity(Pod(uid="pod-2", name="worker-abc", namespace="prod",
                     phase="Running"))
    g.add_entity(Deployment(uid="dep-1", name="api", namespace="prod",
                             replicas=3, ready_replicas=1))
    g.add_entity(Service(uid="svc-1", name="api-svc", namespace="prod"))
    return g


@pytest.fixture
def store(small_graph):
    s = FAISSStore(embedder=Embedder())
    s.index_graph(small_graph)
    return s


class TestFAISSStoreIndex:
    def test_size_after_index(self, store, small_graph):
        assert store.size == len(list(small_graph.entities()))

    def test_empty_graph_is_noop(self):
        s = FAISSStore(embedder=Embedder())
        s.index_graph(OntologyGraph())
        assert s.size == 0

    def test_metadata_has_uid_entries(self, store):
        assert store.search_by_uid("pod-1") is not None
        assert store.search_by_uid("dep-1") is not None


class TestFAISSStoreSearch:
    def test_returns_list(self, store):
        results = store.search("failed pod crashloop", top_k=2)
        assert isinstance(results, list)
        assert len(results) == 2

    def test_result_has_required_keys(self, store):
        r = store.search("pod", top_k=1)[0]
        for key in ("uid", "name", "kind", "namespace", "text", "score"):
            assert key in r

    def test_score_is_float(self, store):
        r = store.search("pod", top_k=1)[0]
        assert isinstance(r["score"], float)

    def test_top_k_limits_results(self, store):
        assert len(store.search("pod", top_k=1)) == 1

    def test_search_empty_store_returns_empty(self):
        s = FAISSStore(embedder=Embedder())
        assert s.search("anything") == []

    def test_search_by_uid_found(self, store):
        r = store.search_by_uid("pod-1")
        assert r is not None
        assert r["uid"] == "pod-1"
        assert r["name"] == "api-xyz"

    def test_search_by_uid_missing(self, store):
        assert store.search_by_uid("does-not-exist") is None


class TestFAISSStoreAddEntity:
    def test_add_entity_increases_size(self):
        s = FAISSStore(embedder=Embedder())
        s.add_entity(Pod(uid="p1", name="pod-1"))
        assert s.size == 1

    def test_add_multiple_entities(self):
        s = FAISSStore(embedder=Embedder())
        s.add_entity(Pod(uid="p1", name="pod-1"))
        s.add_entity(Pod(uid="p2", name="pod-2"))
        assert s.size == 2

    def test_add_entity_searchable_by_uid(self):
        s = FAISSStore(embedder=Embedder())
        s.add_entity(Pod(uid="crash-uid", name="crash-pod", phase="Failed"))
        assert s.search_by_uid("crash-uid") is not None

    def test_add_entity_with_kube_version(self):
        s = FAISSStore(embedder=Embedder())
        s.add_entity(Pod(uid="p1", name="pod-1"), kube_version="v1.28.0")
        r = s.search_by_uid("p1")
        assert r["kube_version"] == "v1.28.0"


class TestFAISSStorePersistence:
    def test_save_and_load_roundtrip(self, store):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.faiss"
            store.save(path)

            s2 = FAISSStore(embedder=Embedder())
            s2.load(path)

            assert s2.size == store.size
            assert s2.search_by_uid("pod-1") is not None
            assert s2.search_by_uid("dep-1") is not None

    def test_save_creates_meta_file(self, store):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "idx.faiss"
            store.save(path)
            assert path.with_suffix(".meta.pkl").exists()

    def test_load_missing_raises(self):
        s = FAISSStore(embedder=Embedder())
        with pytest.raises(FileNotFoundError):
            s.load("/tmp/definitely_absent_kubeverdict_test.faiss")

    def test_loaded_store_is_searchable(self, store):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.faiss"
            store.save(path)

            s2 = FAISSStore(embedder=Embedder())
            s2.load(path)
            results = s2.search("deployment api", top_k=2)
            assert len(results) >= 1


class TestFAISSStoreSummary:
    def test_summary_contains_store_name(self, store):
        assert "FAISSStore" in store.summary()

    def test_summary_contains_kind(self, store):
        assert "Pod" in store.summary()

    def test_summary_empty_store(self):
        s = FAISSStore(embedder=Embedder())
        assert "empty" in s.summary()

    def test_summary_contains_vector_count(self, store):
        assert str(store.size) in store.summary()
