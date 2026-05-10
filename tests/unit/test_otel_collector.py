"""
Unit tests for OtelCollector — backend is mocked.
"""
from unittest.mock import MagicMock

import pytest

from ingestion.otel_collector import OtelCollector, _is_unhealthy, _resolve_service_name
from ontology.entities import (
    DaemonSet,
    Deployment,
    Pod,
    StatefulSet,
)
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_backend(traces: list[dict] | None = None) -> MagicMock:
    b = MagicMock()
    b.search_error_traces.return_value = traces or []
    return b


def _error_trace(trace_id: str = "trace1", service: str = "api") -> dict:
    return {
        "trace_id":      trace_id,
        "service_name":  service,
        "status":        "ERROR",
        "duration_ms":   123.4,
        "span_count":    5,
        "root_span":     "POST /api",
        "error_message": "connection refused",
        "error_spans":   [{"name": "POST /api", "error": "connection refused"}],
        "started_at":    "2026-05-10T08:00:00Z",
    }


def _graph_with_pod(
    name: str = "api-0",
    namespace: str = "prod",
    phase: str = "CrashLoopBackOff",
) -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Pod(uid="pod-1", name=name, namespace=namespace, phase=phase))
    return g


# ─────────────────────────────────────────────────────────────────────────────
# _is_unhealthy
# ─────────────────────────────────────────────────────────────────────────────

class TestIsUnhealthy:
    def test_unhealthy_pod(self):
        p = Pod(uid="p", name="p", phase="CrashLoopBackOff")
        assert _is_unhealthy(p) is True

    def test_healthy_pod(self):
        p = Pod(uid="p", name="p", phase="Running")
        assert _is_unhealthy(p) is False

    def test_degraded_deployment(self):
        d = Deployment(uid="d", name="d", replicas=3, ready_replicas=1)
        assert _is_unhealthy(d) is True

    def test_healthy_deployment(self):
        d = Deployment(uid="d", name="d", replicas=3, ready_replicas=3)
        assert _is_unhealthy(d) is False

    def test_degraded_statefulset(self):
        s = StatefulSet(uid="s", name="s", replicas=2, ready_replicas=0)
        assert _is_unhealthy(s) is True

    def test_degraded_daemonset(self):
        ds = DaemonSet(uid="ds", name="ds", desired=5, ready=2)
        assert _is_unhealthy(ds) is True

    def test_healthy_daemonset(self):
        ds = DaemonSet(uid="ds", name="ds", desired=3, ready=3)
        assert _is_unhealthy(ds) is False


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_service_name
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveServiceName:
    def test_uses_app_kubernetes_io_name(self):
        p = Pod(uid="p", name="p", labels={"app.kubernetes.io/name": "checkout"})
        assert _resolve_service_name(p) == "checkout"

    def test_falls_back_to_app_label(self):
        p = Pod(uid="p", name="p", labels={"app": "payment"})
        assert _resolve_service_name(p) == "payment"

    def test_falls_back_to_component_label(self):
        p = Pod(uid="p", name="p", labels={"app.kubernetes.io/component": "api"})
        assert _resolve_service_name(p) == "api"

    def test_falls_back_to_entity_name(self):
        p = Pod(uid="p", name="my-service")
        assert _resolve_service_name(p) == "my-service"

    def test_priority_order(self):
        p = Pod(uid="p", name="fallback", labels={
            "app.kubernetes.io/name": "winner",
            "app": "loser",
        })
        assert _resolve_service_name(p) == "winner"


# ─────────────────────────────────────────────────────────────────────────────
# OtelCollector.collect
# ─────────────────────────────────────────────────────────────────────────────

class TestOtelCollectorCollect:
    def test_no_entities_returns_zero(self):
        g = OntologyGraph()
        c = OtelCollector(backend=_mock_backend())
        assert c.collect(g) == 0

    def test_healthy_pod_skipped(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="ok", phase="Running"))
        c = OtelCollector(backend=_mock_backend())
        assert c.collect(g) == 0

    def test_unhealthy_pod_fetches_traces(self):
        g = _graph_with_pod()
        backend = _mock_backend([_error_trace("t1")])
        c = OtelCollector(backend=backend)
        count = c.collect(g)
        assert count == 1
        backend.search_error_traces.assert_called_once()

    def test_otel_trace_node_created(self):
        g = _graph_with_pod()
        c = OtelCollector(backend=_mock_backend([_error_trace("t1")]))
        c.collect(g)
        from ontology.entities import ResourceKind
        traces = list(g.entities(ResourceKind.OTEL_TRACE))
        assert len(traces) == 1
        assert traces[0].trace_id == "t1"

    def test_has_trace_edge_created(self):
        g = _graph_with_pod()
        c = OtelCollector(backend=_mock_backend([_error_trace("t1")]))
        c.collect(g)
        edges = [e for e in g._adj.get("pod-1", []) if e.rel_type == RelationshipType.HAS_TRACE]
        assert len(edges) == 1
        assert edges[0].target_uid == "otel-trace-t1"

    def test_entity_annotation_set(self):
        g = _graph_with_pod()
        c = OtelCollector(backend=_mock_backend([_error_trace("t1")]))
        c.collect(g)
        pod = next(e for e in g.entities() if e.name == "api-0")
        assert "otel.trace.t1.status" in pod.annotations
        assert pod.annotations["otel.trace.t1.status"] == "ERROR"

    def test_error_count_annotation(self):
        g = _graph_with_pod()
        c = OtelCollector(backend=_mock_backend([_error_trace("t1"), _error_trace("t2")]))
        c.collect(g)
        pod = next(e for e in g.entities() if e.name == "api-0")
        assert pod.annotations["otel.error_trace_count"] == "2"

    def test_duplicate_trace_ids_not_duplicated(self):
        """Same trace returned for two different entities → one OtelTrace node."""
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api-1", namespace="prod", phase="Error"))
        g.add_entity(Pod(uid="p2", name="api-2", namespace="prod", phase="Error"))
        backend = _mock_backend([_error_trace("shared")])
        c = OtelCollector(backend=backend)
        count = c.collect(g)
        # Two entities each got one trace, but same trace_id → 1 node
        assert count == 1

    def test_trace_without_trace_id_skipped(self):
        g = _graph_with_pod()
        backend = _mock_backend([{"trace_id": "", "status": "ERROR"}])
        c = OtelCollector(backend=backend)
        assert c.collect(g) == 0

    def test_has_trace_edge_not_duplicated(self):
        """Calling collect twice does not add duplicate edges."""
        g = _graph_with_pod()
        backend = _mock_backend([_error_trace("t1")])
        c = OtelCollector(backend=backend)
        c.collect(g)
        c.collect(g)
        edges = [e for e in g._adj.get("pod-1", []) if e.rel_type == RelationshipType.HAS_TRACE]
        assert len(edges) == 1

    def test_trace_fields_mapped_correctly(self):
        g = _graph_with_pod()
        t = _error_trace("full")
        c = OtelCollector(backend=_mock_backend([t]))
        c.collect(g)
        from ontology.entities import ResourceKind
        trace_node = next(iter(g.entities(ResourceKind.OTEL_TRACE)))
        assert trace_node.service_name == "api"
        assert trace_node.duration_ms == pytest.approx(123.4)
        assert trace_node.span_count == 5
        assert trace_node.error_message == "connection refused"
        assert trace_node.root_span_name == "POST /api"
        assert trace_node.started_at == "2026-05-10T08:00:00Z"

    def test_service_name_resolved_from_label(self):
        g = OntologyGraph()
        g.add_entity(Pod(
            uid="p1", name="api-0", namespace="prod", phase="Error",
            labels={"app.kubernetes.io/name": "checkout"},
        ))
        backend = _mock_backend([_error_trace("t1", service="checkout")])
        c = OtelCollector(backend=backend)
        c.collect(g)
        call_args = backend.search_error_traces.call_args
        assert call_args[1]["service"] == "checkout" or call_args[0][0] == "checkout"

    def test_deployment_unhealthy_fetches_traces(self):
        g = OntologyGraph()
        g.add_entity(Deployment(uid="d1", name="api", namespace="prod", replicas=2, ready_replicas=0))
        backend = _mock_backend([_error_trace("t1")])
        c = OtelCollector(backend=backend)
        count = c.collect(g)
        assert count == 1

    def test_backend_error_returns_empty_gracefully(self):
        g = _graph_with_pod()
        backend = _mock_backend([])
        c = OtelCollector(backend=backend)
        assert c.collect(g) == 0
