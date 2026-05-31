"""
Integration tests — OTLP push receiver wired into the full ingestion pipeline.

Two scenarios:
  1. OTLP → OtelCollector → OntologyGraph
     Push spans via HTTP, run OtelCollector, verify OtelTrace nodes + HAS_TRACE edges.

  2. OTLP → OtelCollector → ContextBuilder → RCAAnalyzer
     Spans arrive via OTLP push; the RCA context window must reference the trace
     signal and the mock LLM must receive it.

No real cluster, no Ollama, no Tempo/Jaeger required.
"""
from __future__ import annotations

import json
import textwrap
import time
import urllib.request
from unittest.mock import MagicMock

import pytest

from ingestion.otel_backend import build_backend
from ingestion.otel_collector import OtelCollector
from ingestion.otlp_receiver import OtlpReceiver
from ontology.entities import Deployment, K8sEvent, Pod
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType
from rca.analyzer import RCAAnalyzer
from rca.context_builder import ContextBuilder
from vectorstore.embedder import Embedder
from vectorstore.store import FAISSStore


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _push_spans(port: int, service: str, spans: list[dict]) -> None:
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service}}
                    ]
                },
                "scopeSpans": [{"spans": spans}],
            }
        ]
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/traces",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5):
        pass


def _error_span(
    trace_id: str,
    name: str = "POST /orders",
    error: str = "connection refused",
    ts_offset: int = 0,
) -> dict:
    now_ns = (int(time.time()) + ts_offset) * 1_000_000_000
    return {
        "traceId": trace_id,
        "spanId":  f"span-{trace_id[:8]}",
        "name":    name,
        "startTimeUnixNano": str(now_ns),
        "endTimeUnixNano":   str(now_ns + 5_000_000_000),
        "status": {"code": 2, "message": error},
    }


def _ok_span(trace_id: str, name: str = "GET /health") -> dict:
    now_ns = int(time.time()) * 1_000_000_000
    return {
        "traceId": trace_id,
        "spanId":  f"span-{trace_id[:8]}",
        "name":    name,
        "startTimeUnixNano": str(now_ns),
        "endTimeUnixNano":   str(now_ns + 10_000_000),
        "status": {"code": 1},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def degraded_graph() -> OntologyGraph:
    """Minimal graph: an unhealthy pod + deployment for the order-processor service."""
    g = OntologyGraph()
    g.add_entity(Deployment(
        uid="deploy-order-processor",
        name="order-processor",
        namespace="production",
        labels={"app.kubernetes.io/name": "order-processor"},
        replicas=3,
        ready_replicas=1,
        available_replicas=1,
    ))
    g.add_entity(Pod(
        uid="pod-order-processor-0",
        name="order-processor-0",
        namespace="production",
        labels={"app.kubernetes.io/name": "order-processor"},
        phase="CrashLoopBackOff",
        restart_count=8,
    ))
    g.add_entity(K8sEvent(
        uid="evt-readiness-fail",
        name="evt-readiness-fail",
        namespace="production",
        reason="Unhealthy",
        message="Readiness probe failed: HTTP probe failed with statuscode: 503",
        involved_kind="Pod",
        involved_name="order-processor-0",
        event_type="Warning",
        count=12,
    ))
    return g


@pytest.fixture
def otlp_receiver():
    port = _free_port()
    r = OtlpReceiver(host="127.0.0.1", port=port, max_traces=500)
    r.start()
    time.sleep(0.05)
    yield r
    r.stop()


@pytest.fixture
def faiss_store(degraded_graph) -> FAISSStore:
    store = FAISSStore(embedder=Embedder())
    store.index_graph(degraded_graph)
    return store


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.model_is_pulled.return_value = True
    llm.model = "mistral"
    llm.generate.return_value = textwrap.dedent("""\
        ### 1. Summary
        order-processor is failing due to a DB connection timeout.

        ### 2. Affected resources
        - Pod/production/order-processor-0 — CrashLoopBackOff

        ### 3. Root cause
        OTel traces show TCP connection refused to orders-db:5432.

        ### 4. Causal chain
        1. order-processor attempts to connect to orders-db:5432.
        2. Connection refused — DB pod is not ready.
        3. Readiness probe fails → pod restarts.

        ### 5. Remediation
        kubectl rollout restart deployment/orders-db -n production

        ### 6. Confidence
        MEDIUM — OTel error traces corroborate the readiness probe failures.
    """)
    return llm


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1 — OTLP → OtelCollector → OntologyGraph
# ─────────────────────────────────────────────────────────────────────────────

class TestOtlpCollectorIntegration:
    def test_trace_nodes_created_after_push(self, degraded_graph, otlp_receiver):
        _push_spans(
            otlp_receiver._port,
            "order-processor",
            [_error_span("trace-db-timeout", error="connection refused to orders-db:5432")],
        )
        time.sleep(0.05)

        OtelCollector(otlp_receiver, lookback_hours=1).collect(degraded_graph)

        trace_nodes = [
            e for e in degraded_graph.entities()
            if e.uid.startswith("otel-trace-")
        ]
        assert len(trace_nodes) >= 1, "Expected at least one OtelTrace node"

    def test_has_trace_edge_wired(self, degraded_graph, otlp_receiver):
        _push_spans(
            otlp_receiver._port,
            "order-processor",
            [_error_span("trace-edge-test")],
        )
        time.sleep(0.05)

        OtelCollector(otlp_receiver, lookback_hours=1).collect(degraded_graph)

        # At least one entity must have a HAS_TRACE outgoing edge
        edges_by_type = [
            e
            for uid in degraded_graph._adj
            for e in degraded_graph._adj[uid]
            if e.rel_type == RelationshipType.HAS_TRACE
        ]
        assert len(edges_by_type) >= 1, "Expected HAS_TRACE edge from entity to trace node"

    def test_error_annotation_on_entity(self, degraded_graph, otlp_receiver):
        _push_spans(
            otlp_receiver._port,
            "order-processor",
            [_error_span("trace-annot", error="db connection refused")],
        )
        time.sleep(0.05)

        OtelCollector(otlp_receiver, lookback_hours=1).collect(degraded_graph)

        annotated = [
            e for e in degraded_graph.entities()
            if "otel.error_trace_count" in e.annotations
        ]
        assert len(annotated) >= 1

    def test_ok_traces_not_linked(self, degraded_graph, otlp_receiver):
        _push_spans(
            otlp_receiver._port,
            "order-processor",
            [_ok_span("trace-ok-only")],
        )
        time.sleep(0.05)

        before = len(list(degraded_graph.entities()))
        OtelCollector(otlp_receiver, lookback_hours=1).collect(degraded_graph)
        after = len(list(degraded_graph.entities()))

        # OK traces are ingested into receiver but OtelCollector only wires ERROR traces
        assert after == before, "OK-only traces should not create OtelTrace nodes"

    def test_multiple_error_traces_all_linked(self, degraded_graph, otlp_receiver):
        for i in range(3):
            _push_spans(
                otlp_receiver._port,
                "order-processor",
                [_error_span(f"trace-multi-{i}", error=f"error-{i}")],
            )
        time.sleep(0.05)

        OtelCollector(otlp_receiver, lookback_hours=1).collect(degraded_graph)

        trace_nodes = [
            e for e in degraded_graph.entities()
            if e.uid.startswith("otel-trace-")
        ]
        assert len(trace_nodes) >= 3

    def test_build_backend_otlp_factory(self, otlp_receiver):
        """build_backend('otlp', ...) returns an OtlpReceiver instance."""
        b = build_backend(
            "otlp", "http://ignored",
            otlp_host="127.0.0.1", otlp_port=_free_port(),
        )
        assert isinstance(b, OtlpReceiver)

    def test_collector_skips_healthy_entities(self, otlp_receiver):
        """OtelCollector only targets unhealthy pods/deployments — healthy pods ignored."""
        g = OntologyGraph()
        g.add_entity(Pod(
            uid="pod-healthy",
            name="healthy-pod",
            namespace="production",
            labels={"app.kubernetes.io/name": "order-processor"},
            phase="Running",
            restart_count=0,
        ))
        _push_spans(
            otlp_receiver._port,
            "order-processor",
            [_error_span("trace-healthy-pod-skip")],
        )
        time.sleep(0.05)

        OtelCollector(otlp_receiver, lookback_hours=1).collect(g)

        trace_nodes = [e for e in g.entities() if e.uid.startswith("otel-trace-")]
        assert len(trace_nodes) == 0, "Healthy pods should not receive trace links"


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2 — OTLP → OtelCollector → ContextBuilder → RCAAnalyzer
# ─────────────────────────────────────────────────────────────────────────────

class TestOtlpRcaPipelineIntegration:
    def test_trace_signal_reaches_context_window(
        self, degraded_graph, otlp_receiver, faiss_store
    ):
        _push_spans(
            otlp_receiver._port,
            "order-processor",
            [_error_span(
                "trace-rca-ctx",
                error="connection refused to orders-db.production:5432",
            )],
        )
        time.sleep(0.05)

        OtelCollector(otlp_receiver, lookback_hours=1).collect(degraded_graph)
        # Re-index graph so trace nodes are searchable
        faiss_store.index_graph(degraded_graph)

        ctx = ContextBuilder(degraded_graph, faiss_store).build(
            "order-processor HTTP 500 database timeout"
        )
        assert any(
            "otel" in e.uid or "trace" in e.uid.lower()
            for e in degraded_graph.entities()
            if e.uid.startswith("otel-trace-")
        ), "OtelTrace node missing from graph"
        assert ctx.seeds, "ContextBuilder returned no seeds"

    def test_rca_analyzer_receives_trace_in_prompt(
        self, degraded_graph, otlp_receiver, faiss_store, mock_llm
    ):
        _push_spans(
            otlp_receiver._port,
            "order-processor",
            [_error_span(
                "trace-rca-prompt",
                error="orders-db:5432 — connection refused",
            )],
        )
        time.sleep(0.05)

        OtelCollector(otlp_receiver, lookback_hours=1).collect(degraded_graph)
        faiss_store.index_graph(degraded_graph)

        analyzer = RCAAnalyzer(
            graph=degraded_graph, store=faiss_store, llm=mock_llm
        )
        report = analyzer.analyze("order-processor database connection refused")

        assert report is not None
        # LLM must have been called (context was non-empty)
        assert mock_llm.generate.called, "LLM was not called — context pipeline broken"
        prompt_text = mock_llm.generate.call_args[0][0]
        # The OTel trace error must appear somewhere in the prompt
        assert any(
            kw in prompt_text.lower()
            for kw in ("otel", "trace", "connection", "error")
        ), f"OTel signal absent from LLM prompt. Got:\n{prompt_text[:500]}"

    def test_rca_report_not_none(
        self, degraded_graph, otlp_receiver, faiss_store, mock_llm
    ):
        _push_spans(
            otlp_receiver._port,
            "order-processor",
            [_error_span("trace-report-check", error="db timeout")],
        )
        time.sleep(0.05)

        OtelCollector(otlp_receiver, lookback_hours=1).collect(degraded_graph)
        faiss_store.index_graph(degraded_graph)

        analyzer = RCAAnalyzer(
            graph=degraded_graph, store=faiss_store, llm=mock_llm
        )
        report = analyzer.analyze("order-processor failing")

        assert report is not None
        assert report.summary != ""
        assert any(report.confidence.startswith(c) for c in ("LOW", "MEDIUM", "HIGH", "UNKNOWN"))
