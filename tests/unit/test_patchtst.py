"""
Unit tests for PatchTST-based signal anomaly detection.

All tests run on CPU with tiny synthetic signals — no cluster or GPU required.
"""
import numpy as np
import pytest

from signals.patchtst_detector import (
    AnomalyResult,
    PatchTSTDetector,
    SignalSegment,
    _sliding_windows,
)
from signals.analyzer import (
    SignalAnalyzer,
    _restart_signal,
    _readiness_signal,
    _event_signal,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

RNG = np.random.default_rng(0)

def _normal_signal(n=100) -> np.ndarray:
    """Stationary sine wave + small noise — should score as normal."""
    t = np.linspace(0, 4 * np.pi, n)
    return (np.sin(t) + RNG.normal(0, 0.05, n)).astype(np.float32)


def _anomalous_signal(n=100) -> np.ndarray:
    """Normal history then sudden large spike at the end."""
    t = np.linspace(0, 4 * np.pi, n)
    sig = np.sin(t) + RNG.normal(0, 0.05, n)
    # 10x spike in the last 10 % of the signal
    onset = int(n * 0.9)
    sig[onset:] += 10.0
    return sig.astype(np.float32)


def _short_signal(n=20) -> np.ndarray:
    return RNG.normal(0, 1, n).astype(np.float32)


def _flat_signal(n=100) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


@pytest.fixture
def detector():
    """Tiny model — fast on CPU."""
    return PatchTSTDetector(
        patch_length=8,
        context_length=32,
        prediction_length=4,
        d_model=16,
        num_heads=2,
        num_layers=1,
        epochs=5,
        lr=1e-3,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SignalSegment
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalSegment:
    def test_basic_fields(self):
        seg = SignalSegment("uid-1", "restart_count", _normal_signal())
        assert seg.entity_uid == "uid-1"
        assert seg.metric_name == "restart_count"
        assert len(seg.values) == 100

    def test_default_interval(self):
        seg = SignalSegment("uid-1", "cpu", np.zeros(10))
        assert seg.sample_interval_s == 60


# ─────────────────────────────────────────────────────────────────────────────
# AnomalyResult
# ─────────────────────────────────────────────────────────────────────────────

class TestAnomalyResult:
    def test_is_anomalous_critical(self):
        r = AnomalyResult("u", "m", severity="critical", score=5.0, n_points=100, method="patchtst")
        assert r.is_anomalous

    def test_is_anomalous_warning(self):
        r = AnomalyResult("u", "m", severity="warning", score=2.0, n_points=100, method="patchtst")
        assert r.is_anomalous

    def test_not_anomalous_normal(self):
        r = AnomalyResult("u", "m", severity="normal", score=0.5, n_points=100, method="zscore")
        assert not r.is_anomalous

    def test_to_text_contains_fields(self):
        r = AnomalyResult("pod-1", "restart_count", "critical", 4.2, 100, "patchtst")
        text = r.to_text()
        assert "pod-1" in text
        assert "restart_count" in text
        assert "critical" in text
        assert "patchtst" in text


# ─────────────────────────────────────────────────────────────────────────────
# Sliding windows helper
# ─────────────────────────────────────────────────────────────────────────────

class TestSlidingWindows:
    def test_returns_pairs(self):
        sig = np.arange(100, dtype=np.float32)
        windows = _sliding_windows(sig, context_length=32, prediction_length=4)
        assert len(windows) > 0
        for ctx, tgt in windows:
            assert len(ctx) == 32
            assert len(tgt) == 4

    def test_too_short_returns_empty(self):
        sig = np.arange(10, dtype=np.float32)
        windows = _sliding_windows(sig, context_length=32, prediction_length=4)
        assert windows == []


# ─────────────────────────────────────────────────────────────────────────────
# PatchTSTDetector — z-score fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestZScoreFallback:
    def test_short_signal_uses_zscore(self, detector):
        seg = SignalSegment("u", "m", _short_signal(20))
        result = detector.detect(seg)
        assert result.method == "zscore"

    def test_flat_signal_is_normal(self, detector):
        seg = SignalSegment("u", "m", _flat_signal(20))
        result = detector.detect(seg)
        assert result.severity == "normal"

    def test_very_short_signal_is_normal(self, detector):
        seg = SignalSegment("u", "m", np.array([1.0], dtype=np.float32))
        result = detector.detect(seg)
        assert result.severity == "normal"

    def test_zscore_spike_detected(self, detector):
        sig = np.zeros(50, dtype=np.float32)
        sig[-5:] = 100.0  # large spike
        seg = SignalSegment("u", "m", sig)
        result = detector.detect(seg)
        assert result.is_anomalous

    def test_result_has_entity_uid(self, detector):
        seg = SignalSegment("pod-xyz", "restart_count", _short_signal())
        result = detector.detect(seg)
        assert result.entity_uid == "pod-xyz"


# ─────────────────────────────────────────────────────────────────────────────
# PatchTSTDetector — PatchTST path
# ─────────────────────────────────────────────────────────────────────────────

class TestPatchTSTPath:
    def test_long_normal_signal_uses_patchtst(self, detector):
        seg = SignalSegment("u", "m", _normal_signal(100))
        result = detector.detect(seg)
        assert result.method == "patchtst"

    def test_result_score_is_float(self, detector):
        seg = SignalSegment("u", "m", _normal_signal(100))
        result = detector.detect(seg)
        assert isinstance(result.score, float)
        assert result.score >= 0.0

    def test_forecast_shape_matches_prediction_length(self, detector):
        seg = SignalSegment("u", "m", _normal_signal(100))
        result = detector.detect(seg)
        assert len(result.forecast) == detector.prediction_length
        assert len(result.actual) == detector.prediction_length

    def test_anomalous_signal_higher_score_than_normal(self, detector):
        normal_seg = SignalSegment("u", "m", _normal_signal(100))
        anomalous_seg = SignalSegment("u", "m", _anomalous_signal(100))
        normal_result = detector.detect(normal_seg)
        anomalous_result = detector.detect(anomalous_seg)
        assert anomalous_result.score > normal_result.score

    def test_severity_thresholds(self, detector):
        """Severity escalates with score."""
        assert detector._severity(0.5) == "normal"
        assert detector._severity(detector.warning_threshold + 0.1) == "warning"
        assert detector._severity(detector.critical_threshold + 0.1) == "critical"

    def test_flat_long_signal_not_critical(self, detector):
        seg = SignalSegment("u", "m", _flat_signal(100))
        result = detector.detect(seg)
        assert result.severity != "critical"


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic signal generators
# ─────────────────────────────────────────────────────────────────────────────

class TestSyntheticGenerators:
    def test_restart_signal_length(self):
        sig = _restart_signal(5, 100, np.random.default_rng(0))
        assert len(sig) == 100

    def test_restart_signal_zero_at_start(self):
        sig = _restart_signal(10, 100, np.random.default_rng(0))
        assert sig[:50].mean() < sig[80:].mean()  # end is higher than start

    def test_readiness_signal_degrades(self):
        sig = _readiness_signal(0.3, 100, np.random.default_rng(0))
        assert sig[:70].mean() > sig[85:].mean()  # early is near 1.0, late degrades

    def test_event_signal_spikes_at_end(self):
        sig = _event_signal(50, 100, np.random.default_rng(0))
        assert sig[90:].mean() > sig[:60].mean()

    def test_restart_zero_gives_flat_signal(self):
        sig = _restart_signal(0, 100, np.random.default_rng(0))
        assert sig.max() < 1.0  # no ramp, just noise near 0


# ─────────────────────────────────────────────────────────────────────────────
# SignalAnalyzer integration
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalAnalyzer:
    def test_analyze_returns_results(self, synthetic_graph):
        analyzer = SignalAnalyzer(
            detector=PatchTSTDetector(
                context_length=32, prediction_length=4, d_model=16,
                num_heads=2, num_layers=1, epochs=3,
            )
        )
        results = analyzer.analyze(synthetic_graph)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_results_are_anomaly_results(self, synthetic_graph):
        analyzer = SignalAnalyzer(
            detector=PatchTSTDetector(
                context_length=32, prediction_length=4, d_model=16,
                num_heads=2, num_layers=1, epochs=3,
            )
        )
        for r in analyzer.analyze(synthetic_graph):
            assert isinstance(r, AnomalyResult)

    def test_anomalous_entities_are_annotated(self, synthetic_graph):
        analyzer = SignalAnalyzer(
            detector=PatchTSTDetector(
                context_length=32, prediction_length=4, d_model=16,
                num_heads=2, num_layers=1, epochs=3,
            )
        )
        analyzer.analyze(synthetic_graph)
        # synthetic_graph has a pod with restarts=15 — should get a signal annotation
        from ontology.entities import ResourceKind
        pods = list(synthetic_graph.entities(ResourceKind.POD))
        annotated = [p for p in pods if any(k.startswith("signal.") for k in p.annotations)]
        assert len(annotated) > 0

    def test_signal_annotation_in_to_text(self, synthetic_graph):
        analyzer = SignalAnalyzer(
            detector=PatchTSTDetector(
                context_length=32, prediction_length=4, d_model=16,
                num_heads=2, num_layers=1, epochs=3,
            )
        )
        analyzer.analyze(synthetic_graph)
        from ontology.entities import ResourceKind
        for pod in synthetic_graph.entities(ResourceKind.POD):
            if any(k.startswith("signal.") for k in pod.annotations):
                assert "SIGNAL=[" in pod.to_text()
                break
