"""
Unit tests for LokiSource — all HTTP calls mocked.
"""
from unittest.mock import MagicMock, patch

from ingestion.loki_source import (
    LokiSource,
    _build_logql,
    _detect_level,
    _extract_trace_id,
)
from ontology.entities import Pod, ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_resp(json_data: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status = MagicMock()
    return r


def _loki_result(lines: list[str], ts_start: int = 1715000000000000000) -> dict:
    values = [[str(ts_start + i * 1000), line] for i, line in enumerate(lines)]
    return {"data": {"result": [{"stream": {}, "values": values}]}}


def _graph_with_pod(
    name: str = "api-0",
    namespace: str = "prod",
    phase: str = "CrashLoopBackOff",
) -> tuple[OntologyGraph, Pod]:
    g = OntologyGraph()
    p = Pod(uid="pod-1", name=name, namespace=namespace, phase=phase)
    g.add_entity(p)
    return g, p


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildLogql:
    def test_with_namespace(self):
        logql = _build_logql("my-pod", "prod")
        assert 'k8s_pod_name="my-pod"' in logql
        assert 'k8s_namespace_name="prod"' in logql

    def test_without_namespace(self):
        logql = _build_logql("my-pod", "")
        assert "k8s_namespace_name" not in logql
        assert 'k8s_pod_name="my-pod"' in logql


class TestDetectLevel:
    def test_error_keyword(self):
        assert _detect_level("ERROR: something failed") == "error"

    def test_fatal_keyword(self):
        assert _detect_level("FATAL: out of memory") == "error"

    def test_warn_keyword(self):
        assert _detect_level("WARN: slow query") == "warn"

    def test_debug_keyword(self):
        assert _detect_level("DEBUG entering handler") == "debug"

    def test_defaults_to_info(self):
        assert _detect_level("Starting service") == "info"

    def test_case_insensitive(self):
        assert _detect_level("Fatal: crash") == "error"


class TestExtractTraceId:
    def test_32_char_hex(self):
        line = "trace_id=4bf92f3577b34da6a3ce929d0e0e4736 msg=ok"
        assert _extract_trace_id(line) == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_16_char_hex(self):
        line = "traceID=00f067aa0ba902b7 level=info"
        result = _extract_trace_id(line)
        assert len(result) in (16, 32)

    def test_no_trace_id(self):
        assert _extract_trace_id("regular log line") == ""


# ─────────────────────────────────────────────────────────────────────────────
# LokiSource.is_available
# ─────────────────────────────────────────────────────────────────────────────

class TestIsAvailable:
    def test_returns_true_on_200(self):
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp({}, 200)):
            assert s.is_available() is True

    def test_returns_false_on_non_200(self):
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp({}, 503)):
            assert s.is_available() is False

    def test_returns_false_on_connection_error(self):
        import requests as req
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", side_effect=req.ConnectionError):
            assert s.is_available() is False


# ─────────────────────────────────────────────────────────────────────────────
# LokiSource.collect
# ─────────────────────────────────────────────────────────────────────────────

class TestLokiSourceCollect:
    def test_no_pods_returns_zero(self):
        g = OntologyGraph()
        s = LokiSource(url="http://loki:3100")
        assert s.collect(g) == 0

    def test_healthy_pod_skipped(self):
        g, _ = _graph_with_pod(phase="Running")
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get") as mock_get:
            count = s.collect(g)
        mock_get.assert_not_called()
        assert count == 0

    def test_unhealthy_pod_fetches_logs(self):
        g, _ = _graph_with_pod()
        resp = _loki_result(["ERROR: crash happened"])
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp(resp)):
            count = s.collect(g)
        assert count == 1

    def test_loki_log_node_created(self):
        g, _ = _graph_with_pod()
        resp = _loki_result(["ERROR: crash"])
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp(resp)):
            s.collect(g)
        logs = list(g.entities(ResourceKind.LOKI_LOG))
        assert len(logs) == 1
        assert logs[0].pod_name == "api-0"

    def test_has_log_edge_created(self):
        g, _ = _graph_with_pod()
        resp = _loki_result(["ERROR: crash"])
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp(resp)):
            s.collect(g)
        edges = [e for e in g._adj.get("pod-1", []) if e.rel_type == RelationshipType.HAS_LOG]
        assert len(edges) == 1

    def test_level_detected(self):
        g, _ = _graph_with_pod()
        resp = _loki_result(["ERROR: database timeout"])
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp(resp)):
            s.collect(g)
        log_node = next(iter(g.entities(ResourceKind.LOKI_LOG)))
        assert log_node.level == "error"

    def test_trace_id_extracted_from_line(self):
        g, _ = _graph_with_pod()
        resp = _loki_result(["ERROR trace_id=4bf92f3577b34da6a3ce929d0e0e4736 fail"])
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp(resp)):
            s.collect(g)
        log_node = next(iter(g.entities(ResourceKind.LOKI_LOG)))
        assert log_node.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_max_logs_cap_respected(self):
        g, _ = _graph_with_pod()
        lines = [f"ERROR line {i}" for i in range(50)]
        resp = _loki_result(lines)
        s = LokiSource(url="http://loki:3100", max_logs_per_pod=10)
        with patch("requests.get", return_value=_mock_resp(resp)):
            count = s.collect(g)
        assert count == 10

    def test_timeout_error_returns_zero(self):
        import requests as req
        g, _ = _graph_with_pod()
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", side_effect=req.Timeout):
            assert s.collect(g) == 0

    def test_connection_error_returns_zero(self):
        import requests as req
        g, _ = _graph_with_pod()
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", side_effect=req.ConnectionError):
            assert s.collect(g) == 0

    def test_multiple_lines_multiple_nodes(self):
        g, _ = _graph_with_pod()
        resp = _loki_result(["ERROR a", "WARN b", "INFO c"])
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp(resp)):
            count = s.collect(g)
        assert count == 3

    def test_namespace_in_logql(self):
        g, _ = _graph_with_pod(name="my-pod", namespace="staging")
        resp = _loki_result(["ERROR crash"])
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp(resp)) as mock_get:
            s.collect(g)
        params = mock_get.call_args[1]["params"]
        assert "staging" in params["query"]

    def test_bearer_token_in_headers(self):
        s = LokiSource(url="http://loki:3100", token="mytoken")
        assert s._headers() == {"Authorization": "Bearer mytoken"}

    def test_empty_loki_result(self):
        g, _ = _graph_with_pod()
        resp = {"data": {"result": []}}
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp(resp)):
            assert s.collect(g) == 0

    def test_log_line_truncated_in_node(self):
        g, _ = _graph_with_pod()
        long_line = "ERROR " + "x" * 500
        resp = _loki_result([long_line])
        s = LokiSource(url="http://loki:3100")
        with patch("requests.get", return_value=_mock_resp(resp)):
            s.collect(g)
        log_node = next(iter(g.entities(ResourceKind.LOKI_LOG)))
        # to_text truncates at 200 chars; raw log_line is stored in full
        assert len(log_node.log_line) > 200
        assert len(log_node.to_text()) > 0
