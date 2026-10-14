"""Provenance for the diagnosis — docs/target/knowledge-sources.md § Step 1."""
from __future__ import annotations

import json

import pytest

from knowledge.provenance import classify_url
from tests.unit.test_chart_store import _FakeStore, _manifests_source


@pytest.mark.parametrize("url", [
    "https://charts.bitnami.com/bitnami",
    "oci://registry-1.docker.io/bitnamicharts",
    "oci://registry-1.docker.io/bitnamicharts/redis",
    "https://prometheus-community.github.io/helm-charts",
    "https://kubernetes.github.io/ingress-nginx",
    "charts.jetstack.io",
])
def test_public_sources(url):
    assert classify_url(url) == "public"


@pytest.mark.parametrize("url", [
    "", "oci://registry.acme.internal/charts", "https://charts.acme.corp",
    "oci://registry-1.docker.io/acme-private",            # same registry, not the public path
    "https://charts.bitnami.com.evil.example/bitnami",    # host must match exactly
    "https://ghcr.io/acme/charts",                         # ambiguous host → enterprise
])
def test_unknown_or_private_is_enterprise(url):
    assert classify_url(url) == "enterprise"


def test_public_list_is_configurable(monkeypatch):
    monkeypatch.setenv("PUBLIC_CHART_SOURCES", "charts.acme.corp, oci://registry.acme/public")
    assert classify_url("https://charts.acme.corp/x") == "public"
    assert classify_url("oci://registry.acme/public/redis") == "public"
    assert classify_url("https://charts.bitnami.com/bitnami") == "enterprise"


def _store(tmp_path):
    from knowledge.chart_store import ChartStore
    return ChartStore(tmp_path / "charts")


def test_push_derives_origin_from_source_url(tmp_path):
    src = _manifests_source(tmp_path / "src", replicas=2)
    store = _store(tmp_path)
    up = store.push("redis", "18.1.0", src, render_type="manifests", source="repo",
                    source_url="https://charts.bitnami.com/bitnami")
    own = store.push("payments", "4.2.0", src, render_type="manifests")
    assert (up.origin, own.origin) == ("public", "enterprise")
    assert store.get("redis", "18.1.0").origin == "public"


def test_explicit_origin_wins_and_is_validated(tmp_path):
    src = _manifests_source(tmp_path / "src", replicas=2)
    store = _store(tmp_path)
    c = store.push("redis", "1", src, render_type="manifests", origin="enterprise",
                   source_url="https://charts.bitnami.com/bitnami")
    assert c.origin == "enterprise"
    with pytest.raises(ValueError):
        store.push("x", "1", src, render_type="manifests", origin="secret")


def test_meta_written_before_provenance_loads_as_enterprise(tmp_path):
    src = _manifests_source(tmp_path / "src", replicas=2)
    store = _store(tmp_path)
    store.push("old", "1", src, render_type="manifests")
    meta = tmp_path / "charts" / "old" / "1" / ".kv-meta.json"
    data = json.loads(meta.read_text())
    data.pop("origin")
    data.pop("source_url")
    meta.write_text(json.dumps(data))
    assert store.get("old", "1").origin == "enterprise"


def test_chart_anchors_are_indexed_with_their_origin(tmp_path):
    from knowledge.chart_indexer import ChartIndexer
    src = _manifests_source(tmp_path / "src", replicas=2)
    store = _store(tmp_path)
    pub = store.push("redis", "1", src, render_type="manifests",
                     source_url="https://charts.bitnami.com/bitnami")
    own = store.push("payments", "1", src, render_type="manifests")
    fake = _FakeStore()
    ChartIndexer(fake).index_chart(store, pub)
    ChartIndexer(fake).index_chart(store, own)
    assert fake.doc_sources == ["public", "enterprise"]
    assert "origin=public" in fake.entities[0].to_text()


def test_docs_and_examples_carry_their_source():
    from knowledge.doc_indexer import DocIndexer
    from knowledge.doc_store import EnterpriseDoc
    from knowledge.example_store import ExampleIndexer, ResolvedIncident
    fake = _FakeStore()
    DocIndexer(fake).index_doc(EnterpriseDoc(title="runbook", content="restart the pods"))
    ExampleIndexer(fake).index_example(ResolvedIncident(query="pods oom", root_cause="limit"))
    assert fake.doc_sources == ["enterprise", "example"]


def test_helm_repository_origin_in_context_text():
    from ontology.entities import HelmRepository
    repo = HelmRepository(uid="helmrepo-bitnami", name="bitnami",
                          url="https://charts.bitnami.com/bitnami", origin="public")
    assert "origin=public" in repo.to_text()
    assert "origin=enterprise" in HelmRepository(uid="r", name="acme").to_text()


class _Chunk:
    def __init__(self, uid, text):
        self.uid, self.name, self.namespace, self.kind, self._t = uid, uid, "ns", "Doc", text

    def to_text(self):
        return self._t


def test_enterprise_outranks_public_at_equal_similarity():
    from vectorstore.store import FAISSStore
    store = FAISSStore()
    text = "redis maxmemory 2gb eviction policy allkeys-lru"
    store.add_entity(_Chunk("pub", text), doc_source="public")
    store.add_entity(_Chunk("ent", text), doc_source="enterprise")
    assert [h["uid"] for h in store.search(text, top_k=2)] == ["ent", "pub"]
    assert [h["uid"] for h in store.hybrid_search(text, top_k=2)][0] == "ent"
