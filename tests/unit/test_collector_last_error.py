"""Collectors record backend request failures in ``last_error``.

They keep returning 0 / [] on a failed request (callers that only read the
count are unchanged), but the workflow nodes read ``last_error`` to report an
unreachable backend as a fallback instead of "collected, found nothing".
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from ingestion.loki_source import LokiSource
from ingestion.otel_backend import TempoBackend
from ingestion.otel_collector import OtelCollector
from ingestion.prometheus_collector import PrometheusCollector
from ontology.entities import Pod
from ontology.graph import OntologyGraph

_DOWN = requests.ConnectionError("connection refused")


def _graph_with_not_ready_pod() -> OntologyGraph:
    graph = OntologyGraph()
    graph.add_entity(Pod(uid="p1", name="api-1", namespace="prod", phase="Failed"))
    return graph


class TestPrometheusCollector:
    def test_unreachable_records_error_and_returns_zero(self):
        collector = PrometheusCollector(url="http://prom.invalid")
        with patch("ingestion.prometheus_collector.requests.get", side_effect=_DOWN):
            assert collector.collect(OntologyGraph()) == 0
        assert collector.last_error == "connection refused"

    def test_timeout_is_recorded(self):
        collector = PrometheusCollector(url="http://prom.invalid", timeout=3)
        with patch("ingestion.prometheus_collector.requests.get",
                   side_effect=requests.Timeout()):
            collector.collect(OntologyGraph())
        assert collector.last_error == "timed out after 3s"

    def test_error_is_reset_on_next_collect(self):
        collector = PrometheusCollector(url="http://prom.invalid")
        collector.last_error = "stale"
        ok = MagicMock()
        ok.json.return_value = {"data": {"alerts": []}}
        with patch("ingestion.prometheus_collector.requests.get", return_value=ok):
            collector.collect(OntologyGraph())
        assert collector.last_error is None


class TestLokiSource:
    def test_failed_query_records_error(self):
        source = LokiSource(url="http://loki.invalid")
        with patch("ingestion.loki_source.requests.get", side_effect=_DOWN):
            assert source.collect(_graph_with_not_ready_pod()) == 0
        assert source.last_error == "connection refused"

    def test_error_is_reset_on_next_collect(self):
        source = LokiSource(url="http://loki.invalid")
        source.last_error = "stale"
        with patch.object(LokiSource, "_query", return_value=[]):
            source.collect(_graph_with_not_ready_pod())
        assert source.last_error is None


class TestOtelCollector:
    def test_backend_failure_surfaces_on_the_collector(self):
        collector = OtelCollector(TempoBackend(url="http://tempo.invalid"))
        with patch("ingestion.otel_backend.requests.get", side_effect=_DOWN):
            assert collector.collect(_graph_with_not_ready_pod()) == 0
        assert collector.last_error == "connection refused"

    def test_backend_error_is_reset_on_next_collect(self):
        backend = TempoBackend(url="http://tempo.invalid")
        backend.last_error = "stale"
        collector = OtelCollector(backend)
        with patch.object(TempoBackend, "search_error_traces", return_value=[]):
            collector.collect(_graph_with_not_ready_pod())
        assert collector.last_error is None
        assert backend.last_error is None
