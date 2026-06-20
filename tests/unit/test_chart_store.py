"""
Tests for the versioned enterprise chart / expected-state store + indexer.

Core guardrail: the chart **version is evidence** — pushing two versions of the
same source must produce different expected manifests (and therefore different
anchors). The store/indexer is not limited to Helm: the `manifests` backend
needs no binary, so the version-as-evidence property is proven without helm.
"""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from knowledge.chart_store import ChartStore
from knowledge.chart_indexer import ChartIndexer


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _FakeStore:
    """Captures indexed entities without pulling in FAISS/embeddings."""

    def __init__(self) -> None:
        self.entities: list = []

    def add_entity(self, e) -> None:
        self.entities.append(e)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _manifests_source(root: Path, replicas: int) -> Path:
    """A raw/customised manifests source (no Helm, no binary)."""
    _write(root / "deployment.yaml", f"""\
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: payment-service
          namespace: prod
        spec:
          replicas: {replicas}
          template:
            spec:
              containers:
                - name: payment-service
                  image: payment-service:1.0
    """)
    return root


def _kustomize_source(root: Path, replicas: int) -> Path:
    _write(root / "deployment.yaml", f"""\
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: payment-service
          namespace: prod
        spec:
          replicas: {replicas}
          template:
            spec:
              containers:
                - name: payment-service
                  image: payment-service:1.0
    """)
    _write(root / "kustomization.yaml", """\
        resources:
          - deployment.yaml
    """)
    return root


def _helm_source(root: Path, replica_count: int, chart_version: str) -> Path:
    _write(root / "Chart.yaml", f"""\
        apiVersion: v2
        name: payment-service
        version: {chart_version}
    """)
    _write(root / "values.yaml", f"""\
        replicaCount: {replica_count}
        image:
          repository: payment-service
          tag: "1.0"
    """)
    _write(root / "templates/deployment.yaml", """\
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: payment-service
          namespace: {{ .Release.Namespace }}
        spec:
          replicas: {{ .Values.replicaCount }}
          template:
            spec:
              containers:
                - name: payment-service
                  image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
    """)
    return root


# ─────────────────────────────────────────────────────────────────────────────
# Store CRUD (no binary)
# ─────────────────────────────────────────────────────────────────────────────

class TestChartStore:
    def test_push_and_path(self, tmp_path):
        store = ChartStore(data_dir=tmp_path / "store")
        src = _manifests_source(tmp_path / "src", replicas=2)
        meta = store.push("payment-service", "1.0.0", src, render_type="manifests")
        assert meta.id == "payment-service@1.0.0"
        assert store.path("payment-service", "1.0.0") is not None

    def test_two_versions_coexist(self, tmp_path):
        store = ChartStore(data_dir=tmp_path / "store")
        store.push("payment-service", "1.0.0",
                   _manifests_source(tmp_path / "v1", 2), render_type="manifests")
        store.push("payment-service", "2.0.0",
                   _manifests_source(tmp_path / "v2", 5), render_type="manifests")
        assert store.versions("payment-service") == ["1.0.0", "2.0.0"]
        assert len(store.list()) == 2

    def test_version_is_required(self, tmp_path):
        store = ChartStore(data_dir=tmp_path / "store")
        src = _manifests_source(tmp_path / "src", replicas=2)
        with pytest.raises(ValueError, match="version"):
            store.push("payment-service", "", src, render_type="manifests")

    def test_helm_source_requires_chart_yaml(self, tmp_path):
        store = ChartStore(data_dir=tmp_path / "store")
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ValueError, match="Chart.yaml"):
            store.push("x", "1.0.0", empty, render_type="helm")

    def test_delete(self, tmp_path):
        store = ChartStore(data_dir=tmp_path / "store")
        store.push("payment-service", "1.0.0",
                   _manifests_source(tmp_path / "v1", 2), render_type="manifests")
        assert store.delete("payment-service", "1.0.0") is True
        assert store.path("payment-service", "1.0.0") is None


# ─────────────────────────────────────────────────────────────────────────────
# Indexer — version IS evidence (manifests backend, no binary)
# ─────────────────────────────────────────────────────────────────────────────

class TestChartIndexerManifests:
    def test_indexes_resource_anchor(self, tmp_path):
        store = ChartStore(data_dir=tmp_path / "store")
        chart = store.push("payment-service", "1.0.0",
                           _manifests_source(tmp_path / "v1", 2), render_type="manifests")
        fake = _FakeStore()
        n = ChartIndexer(fake).index_chart(store, chart)
        assert n == 1
        text = fake.entities[0].to_text()
        assert "chart=payment-service" in text
        assert "version=1.0.0" in text
        assert "spec.replicas=2" in text

    def test_version_changes_the_evidence(self, tmp_path):
        """The whole point: two versions → different rendered anchors."""
        store = ChartStore(data_dir=tmp_path / "store")
        c1 = store.push("payment-service", "1.0.0",
                        _manifests_source(tmp_path / "v1", 2), render_type="manifests")
        c2 = store.push("payment-service", "2.0.0",
                        _manifests_source(tmp_path / "v2", 5), render_type="manifests")

        fake = _FakeStore()
        idx = ChartIndexer(fake)
        idx.index_chart(store, c1)
        idx.index_chart(store, c2)

        texts = [e.to_text() for e in fake.entities]
        assert any("version=1.0.0" in t and "spec.replicas=2" in t for t in texts)
        assert any("version=2.0.0" in t and "spec.replicas=5" in t for t in texts)


# ─────────────────────────────────────────────────────────────────────────────
# Indexer — real helm render, version-pinned (guarded)
# ─────────────────────────────────────────────────────────────────────────────

class TestExpectedStateDriftSkill:
    """A/B skills: rendered expected-state (any mode) diffed vs live."""

    def test_rendered_vs_live_drift_manifests(self, tmp_path):
        from mcp_server import _rendered_expected_drift
        from ontology.entities import Deployment
        from ontology.graph import OntologyGraph

        store = ChartStore(data_dir=tmp_path / "charts")
        store.push("payment-service", "1.0.0",
                   _manifests_source(tmp_path / "src", 3), render_type="manifests")

        g = OntologyGraph()
        g.add_entity(Deployment(uid="d1", name="payment-service", namespace="prod",
                                replicas=1, ready_replicas=0))

        chart, items = _rendered_expected_drift(
            g, "payment-service", None, "prod", chart_store=store)
        assert chart is not None and chart.render_type == "manifests"
        repl = [i for i in items if i["field"] == "spec.replicas"]
        assert repl, "expected spec.replicas drift (rendered 3 vs live 1)"
        assert repl[0]["declared"] == "3"
        assert repl[0]["observed"] == "1"
        assert repl[0]["source"] == "rendered"

    def test_no_pushed_source_is_noop(self, tmp_path):
        from mcp_server import _rendered_expected_drift
        from ontology.graph import OntologyGraph

        store = ChartStore(data_dir=tmp_path / "charts")
        chart, items = _rendered_expected_drift(
            OntologyGraph(), "missing", None, "prod", chart_store=store)
        assert chart is None and items == []


@pytest.mark.skipif(
    shutil.which("kustomize") is None and shutil.which("kubectl") is None,
    reason="neither kustomize nor kubectl installed",
)
class TestChartIndexerKustomize:
    def test_kustomize_build_differs_by_version(self, tmp_path):
        store = ChartStore(data_dir=tmp_path / "store")
        c1 = store.push("payment-service", "1.0.0",
                        _kustomize_source(tmp_path / "v1", 2), render_type="kustomize")
        c2 = store.push("payment-service", "2.0.0",
                        _kustomize_source(tmp_path / "v2", 5), render_type="kustomize")

        fake = _FakeStore()
        idx = ChartIndexer(fake)
        idx.index_chart(store, c1)
        idx.index_chart(store, c2)

        texts = [e.to_text() for e in fake.entities]
        assert any("version=1.0.0" in t and "spec.replicas=2" in t for t in texts)
        assert any("version=2.0.0" in t and "spec.replicas=5" in t for t in texts)


# ─────────────────────────────────────────────────────────────────────────────
# Wiring — index_node renders + indexes pushed charts into the evidence store
# ─────────────────────────────────────────────────────────────────────────────

class _DummyConn:
    def close(self) -> None:
        pass


class TestIndexNodeWiring:
    def test_pushed_chart_becomes_an_evidence_anchor(self, tmp_path, monkeypatch):
        from vectorstore.store import FAISSStore
        # Keep it a unit test: no disk index / DB writes.
        monkeypatch.setattr(FAISSStore, "save", lambda self: None)
        monkeypatch.setattr(FAISSStore, "persist_texts", lambda self, conn: None)
        monkeypatch.setattr("persistence.db.get_db", lambda: _DummyConn())

        from ontology.entities import Deployment
        from ontology.graph import OntologyGraph
        from workflow.nodes import index_node

        def _graph():
            g = OntologyGraph()
            g.add_entity(Deployment(uid="d1", name="payment-service",
                                    namespace="prod", replicas=1, ready_replicas=0))
            return g

        # Baseline: identical graph, no pushed charts.
        cfg_a = {"configurable": {"graph": _graph()}}
        index_node({}, cfg_a)
        size_a = cfg_a["configurable"]["store"].size

        # Same graph, but one pushed (manifests) chart in the store.
        store = ChartStore(data_dir=tmp_path / "charts")
        store.push("payment-service", "1.0.0",
                   _manifests_source(tmp_path / "src", 3), render_type="manifests")
        cfg_b = {"configurable": {"graph": _graph(),
                                  "chart_dir": str(tmp_path / "charts")}}
        index_node({}, cfg_b)
        size_b = cfg_b["configurable"]["store"].size

        # Exactly one extra vector: the rendered chart resource anchor.
        assert size_b == size_a + 1


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm binary not installed")
class TestChartIndexerHelm:
    def test_helm_render_differs_by_version(self, tmp_path):
        store = ChartStore(data_dir=tmp_path / "store")
        c1 = store.push("payment-service", "1.0.0",
                        _helm_source(tmp_path / "v1", 2, "1.0.0"), render_type="helm")
        c2 = store.push("payment-service", "2.0.0",
                        _helm_source(tmp_path / "v2", 5, "2.0.0"), render_type="helm")

        fake = _FakeStore()
        idx = ChartIndexer(fake)
        idx.index_chart(store, c1)
        idx.index_chart(store, c2)

        texts = [e.to_text() for e in fake.entities]
        assert any("version=1.0.0" in t and "spec.replicas=2" in t for t in texts)
        assert any("version=2.0.0" in t and "spec.replicas=5" in t for t in texts)
