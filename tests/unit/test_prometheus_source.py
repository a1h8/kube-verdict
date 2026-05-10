"""
Unit tests for PrometheusMetricSource and multi-horizon SignalAnalyzer.
All HTTP calls are mocked.
"""
import time
from unittest.mock import MagicMock, patch

import numpy as np
import requests

from signals.prometheus_source import (
    PrometheusMetricSource,
    HorizonSegment,
)
from signals.analyzer import SignalAnalyzer
from signals.patchtst_detector import PatchTSTDetector, AnomalyResult
from ontology.entities import Pod
from ontology.graph import OntologyGraph


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _range_resp(values: list[float], status: int = 200) -> MagicMock:
    """Mock a Prometheus /api/v1/query_range response with one series."""
    now = int(time.time())
    step = 60
    series = [[now - (len(values) - i) * step, str(v)] for i, v in enumerate(values)]
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"data": {"result": [{"values": series}]}}
    r.raise_for_status = MagicMock()
    return r


def _empty_resp() -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"data": {"result": []}}
    r.raise_for_status = MagicMock()
    return r


def _src() -> PrometheusMetricSource:
    return PrometheusMetricSource(url="http://prom:9090", timeout=5)


# ─────────────────────────────────────────────────────────────────────────────
# _range_query
# ─────────────────────────────────────────────────────────────────────────────

class TestRangeQuery:
    def test_returns_numpy_array(self):
        values = [0.0] * 10 + [1.0, 2.0, 5.0]
        with patch("requests.get", return_value=_range_resp(values)):
            result = _src()._range_query("up", 3600, 60)
        assert isinstance(result, np.ndarray)
        assert len(result) == len(values)

    def test_returns_none_on_empty_result(self):
        with patch("requests.get", return_value=_empty_resp()):
            assert _src()._range_query("up", 3600, 60) is None

    def test_returns_none_on_timeout(self):
        with patch("requests.get", side_effect=requests.Timeout()):
            assert _src()._range_query("up", 3600, 60) is None

    def test_returns_none_on_connection_error(self):
        with patch("requests.get", side_effect=requests.ConnectionError()):
            assert _src()._range_query("up", 3600, 60) is None

    def test_aggregates_multiple_series_by_mean(self):
        """When multiple pods match (e.g. init containers), we take the mean."""
        now = int(time.time())
        series_a = [[now - 2 * 60, "2.0"], [now - 60, "4.0"]]
        series_b = [[now - 2 * 60, "4.0"], [now - 60, "6.0"]]
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"data": {"result": [
            {"values": series_a}, {"values": series_b}
        ]}}
        r.raise_for_status = MagicMock()
        with patch("requests.get", return_value=r):
            result = _src()._range_query("cpu", 3600, 60)
        np.testing.assert_allclose(result, [3.0, 5.0])

    def test_sends_bearer_token(self):
        src = PrometheusMetricSource(url="http://prom:9090", token="tok123")
        with patch("requests.get", return_value=_empty_resp()) as mock_get:
            src._range_query("up", 3600, 60)
        headers = mock_get.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer tok123"

    def test_passes_correct_params(self):
        with patch("requests.get", return_value=_empty_resp()) as mock_get:
            _src()._range_query("metric", 7200, 300)
        params = mock_get.call_args[1]["params"]
        assert params["query"] == "metric"
        assert params["step"] == 300
        assert params["end"] - params["start"] == 7200


# ─────────────────────────────────────────────────────────────────────────────
# pod_segments
# ─────────────────────────────────────────────────────────────────────────────

class TestPodSegments:
    def test_returns_horizon_segments_for_all_horizons(self):
        values = list(range(60))
        with patch("requests.get", return_value=_range_resp(values)):
            segs = _src().pod_segments("p1", "api-0", "prod")
        # 3 horizons × 3 metrics = up to 9; at least some returned
        assert len(segs) > 0
        assert all(isinstance(s, HorizonSegment) for s in segs)

    def test_horizon_labels_are_valid(self):
        values = list(range(60))
        with patch("requests.get", return_value=_range_resp(values)):
            segs = _src().pod_segments("p1", "api-0", "prod")
        horizons = {s.horizon for s in segs}
        assert horizons <= {"short", "medium", "long"}

    def test_skips_series_with_fewer_than_5_points(self):
        values = [1.0, 2.0]  # too short
        with patch("requests.get", return_value=_range_resp(values)):
            segs = _src().pod_segments("p1", "api-0", "prod")
        assert segs == []

    def test_subset_of_horizons(self):
        values = list(range(30))
        with patch("requests.get", return_value=_range_resp(values)):
            segs = _src().pod_segments("p1", "api-0", "prod", horizons=["short"])
        returned_horizons = {s.horizon for s in segs}
        assert "medium" not in returned_horizons
        assert "long" not in returned_horizons

    def test_empty_result_returns_no_segments(self):
        with patch("requests.get", return_value=_empty_resp()):
            segs = _src().pod_segments("p1", "api-0", "prod")
        assert segs == []


# ─────────────────────────────────────────────────────────────────────────────
# deployment_segments / statefulset_segments
# ─────────────────────────────────────────────────────────────────────────────

class TestDeploymentSegments:
    def test_returns_ready_ratio_segments(self):
        values = [1.0] * 50 + [0.5] * 10
        with patch("requests.get", return_value=_range_resp(values)):
            segs = _src().deployment_segments("d1", "api", "prod")
        assert all(s.segment.metric_name == "ready_ratio" for s in segs)

    def test_statefulset_segments_same_metric(self):
        values = [1.0] * 50 + [0.33] * 10
        with patch("requests.get", return_value=_range_resp(values)):
            segs = _src().statefulset_segments("s1", "db", "prod")
        assert all(s.segment.metric_name == "ready_ratio" for s in segs)


# ─────────────────────────────────────────────────────────────────────────────
# SignalAnalyzer with PrometheusMetricSource
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalAnalyzerRealMode:
    def _fast_detector(self):
        """Minimal detector that always returns normal — avoids training time."""
        det = MagicMock(spec=PatchTSTDetector)
        det.detect.return_value = AnomalyResult(
            entity_uid="x", metric_name="restart_count",
            severity="normal", score=0.5, n_points=60, method="zscore",
        )
        return det

    def test_analyze_uses_prometheus_source(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api-0", namespace="prod", restart_count=3))

        src = MagicMock(spec=PrometheusMetricSource)
        src.pod_segments.return_value = [
            HorizonSegment(
                segment=MagicMock(entity_uid="p1", metric_name="restart_count",
                                  values=np.ones(60), sample_interval_s=60),
                horizon="short",
            )
        ]

        analyzer = SignalAnalyzer(detector=self._fast_detector(), prometheus_source=src)
        results = analyzer.analyze(g)
        src.pod_segments.assert_called_once()
        assert len(results) == 1
        assert results[0].horizon == "short"

    def test_analyze_falls_back_to_synthetic_when_no_prom_data(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api-0", namespace="prod", restart_count=5))

        src = MagicMock(spec=PrometheusMetricSource)
        src.pod_segments.return_value = []   # Prometheus returned nothing

        analyzer = SignalAnalyzer(detector=self._fast_detector(), prometheus_source=src)
        results = analyzer.analyze(g)
        assert len(results) == 1
        assert results[0].horizon == ""   # synthetic fallback

    def test_analyze_without_source_uses_synthetic(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api-0", namespace="prod", restart_count=5))
        analyzer = SignalAnalyzer(detector=self._fast_detector())
        results = analyzer.analyze(g)
        assert len(results) == 1
        assert results[0].horizon == ""

    def test_horizon_annotation_includes_horizon_label(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api-0", namespace="prod", restart_count=5))

        src = MagicMock(spec=PrometheusMetricSource)
        src.pod_segments.return_value = [
            HorizonSegment(
                segment=MagicMock(entity_uid="p1", metric_name="restart_count",
                                  values=np.ones(60), sample_interval_s=60),
                horizon="medium",
            )
        ]
        det = MagicMock(spec=PatchTSTDetector)
        det.detect.return_value = AnomalyResult(
            entity_uid="p1", metric_name="restart_count",
            severity="warning", score=2.0, n_points=60, method="zscore",
        )
        analyzer = SignalAnalyzer(detector=det, prometheus_source=src)
        analyzer.analyze(g)

        pod = g.get("p1")
        assert "signal.restart_count.medium" in pod.annotations

    def test_anomaly_annotation_aggregates_horizons(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api-0", namespace="prod", restart_count=5))

        src = MagicMock(spec=PrometheusMetricSource)
        src.pod_segments.return_value = [
            HorizonSegment(
                segment=MagicMock(entity_uid="p1", metric_name="restart_count",
                                  values=np.ones(60), sample_interval_s=60),
                horizon="short",
            ),
            HorizonSegment(
                segment=MagicMock(entity_uid="p1", metric_name="restart_count",
                                  values=np.ones(96), sample_interval_s=900),
                horizon="medium",
            ),
        ]
        det = MagicMock(spec=PatchTSTDetector)
        det.detect.return_value = AnomalyResult(
            entity_uid="p1", metric_name="restart_count",
            severity="critical", score=3.5, n_points=60, method="patchtst",
        )
        analyzer = SignalAnalyzer(detector=det, prometheus_source=src)
        analyzer.analyze(g)

        pod = g.get("p1")
        anomaly = pod.annotations.get("signal.anomaly", "")
        assert "short" in anomaly
        assert "medium" in anomaly


# ─────────────────────────────────────────────────────────────────────────────
# AnomalyResult horizon field
# ─────────────────────────────────────────────────────────────────────────────

class TestAnomalyResultHorizon:
    def test_to_text_includes_horizon_when_set(self):
        r = AnomalyResult(
            entity_uid="p1", metric_name="restart_count",
            severity="critical", score=3.5, n_points=60,
            method="patchtst", horizon="short",
        )
        assert "horizon=short" in r.to_text()

    def test_to_text_omits_horizon_when_empty(self):
        r = AnomalyResult(
            entity_uid="p1", metric_name="restart_count",
            severity="critical", score=3.5, n_points=60,
            method="patchtst", horizon="",
        )
        assert "horizon" not in r.to_text()

    def test_is_anomalous_unaffected_by_horizon(self):
        r = AnomalyResult(
            entity_uid="p1", metric_name="x",
            severity="warning", score=2.0, n_points=60,
            method="zscore", horizon="long",
        )
        assert r.is_anomalous is True
