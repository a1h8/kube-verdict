"""
h015 — etcd compaction latency (the OTLP-fixture wedge).

Unlike h001–h011 (K8s manifest/Helm drift evidence only), this case's root
cause is invisible to K8s events alone: the readiness probe just times out.
The differentiator is OTel error-trace evidence (otel/traces.json) showing
sustained DeadlineExceeded errors against etcd Range calls, correlated to the
pod via HAS_TRACE edges — case_loader._wire_otel_traces / _load_otel
(tests/integration/cases/case_loader.py).

Deterministic — no cluster, no Ollama, no FAISS embedding (a stub store is
enough since ContextBuilder's BFS/anchor/trace sections don't need real
vector search). Runs on every CI run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontology.entities import OtelTrace, Pod, ResourceKind
from ontology.relationships import RelationshipType
from rca.context_builder import ContextBuilder
from tests.integration.cases.case_loader import build_graph, load_case

CASE_DIR = Path(__file__).parent / "cases" / "h015_etcd_compaction"


class _StubStore:
    """No FAISS/embedding needed — this case exercises BFS + trace wiring only."""

    last_retrieval_stats: dict = {}

    def search(self, query, top_k=10):
        return []

    def hybrid_search(self, query, top_k=10):
        return []


@pytest.fixture(scope="module")
def expect() -> dict:
    return json.loads((CASE_DIR / "expect.json").read_text())


@pytest.fixture(scope="module")
def graph():
    case = load_case(CASE_DIR)
    return build_graph(case)


@pytest.fixture(scope="module")
def pod(graph):
    pods = [e for e in graph.entities() if isinstance(e, Pod)]
    assert len(pods) == 1
    return pods[0]


class TestOtelFixtureLoading:
    def test_case_declares_otel_fixture(self):
        traces_file = CASE_DIR / "otel" / "traces.json"
        assert traces_file.exists()
        raw = json.loads(traces_file.read_text())
        assert len(raw) == 4

    def test_four_otel_trace_entities_created(self, graph):
        traces = [
            e for e in graph.entities(ResourceKind.OTEL_TRACE) if isinstance(e, OtelTrace)
        ]
        assert len(traces) == 4

    def test_all_traces_are_error_status(self, graph):
        traces = [
            e for e in graph.entities(ResourceKind.OTEL_TRACE) if isinstance(e, OtelTrace)
        ]
        assert all(t.status == "ERROR" for t in traces)

    def test_traces_mention_etcd_compaction(self, graph):
        traces = [
            e for e in graph.entities(ResourceKind.OTEL_TRACE) if isinstance(e, OtelTrace)
        ]
        assert all("compacted" in t.error_message for t in traces)
        assert all(t.root_span_name == "etcdserverpb.KV/Range" for t in traces)

    def test_duration_far_exceeds_readiness_timeout(self, graph, expect):
        """Traces run ~4.6-5.1s; the readiness probe timeoutSeconds is 2 — the
        trace evidence explains why the probe times out, events alone don't."""
        traces = [
            e for e in graph.entities(ResourceKind.OTEL_TRACE) if isinstance(e, OtelTrace)
        ]
        assert all(t.duration_ms > 4000 for t in traces)


class TestOtelGraphWiring:
    def test_has_trace_edges_from_pod(self, graph, pod):
        neighbors = [
            n for n in graph.neighbors(pod.uid, RelationshipType.HAS_TRACE)
        ]
        assert len(neighbors) == 4
        assert all(isinstance(n, OtelTrace) for n in neighbors)

    def test_pod_annotated_with_trace_status_and_error(self, pod):
        status_keys = [k for k in pod.annotations if k.endswith(".status")]
        error_keys = [k for k in pod.annotations if k.endswith(".error")]
        assert len(status_keys) == 4
        assert len(error_keys) == 4
        assert all(pod.annotations[k] == "ERROR" for k in status_keys)


class TestContextWindowSurfacesTraces:
    """context_builder's OTEL_TRACE query (rca/context_builder.py) is generic —
    it can't tell a fixture-loaded trace from a live-collected one."""

    def test_traces_section_populated(self, graph):
        ctx = ContextBuilder(graph=graph, store=_StubStore()).build("why is inventory-api not ready")
        assert len(ctx.traces) == 4

    def test_traces_section_mentions_etcd(self, graph):
        ctx = ContextBuilder(graph=graph, store=_StubStore()).build("why is inventory-api not ready")
        assert all("etcd" in t.lower() for t in ctx.traces)

    def test_prompt_block_includes_traces_header(self, graph):
        ctx = ContextBuilder(graph=graph, store=_StubStore()).build("why is inventory-api not ready")
        prompt = ctx.to_prompt_block()
        assert "TRACES" in prompt
        assert "etcd" in prompt.lower()
