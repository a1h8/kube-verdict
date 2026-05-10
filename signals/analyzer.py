"""
SignalAnalyzer — integrates PatchTST anomaly detection with the OntologyGraph.

Two operating modes
───────────────────
1. **Real mode** (prometheus_source provided):
   Fetches genuine time series from Prometheus at three horizons:
     short  (1 h / 1 m step)  — is it getting worse right now?
     medium (24 h / 15 m step) — when did it start and what is the trend?
     long   (7 d / 1 h step)  — anomaly vs normal weekly pattern?
   PatchTST trains on real history → meaningful forecast error scores.

2. **Synthetic mode** (no prometheus_source, or Prometheus unavailable):
   Generates plausible history from the current point-in-time K8s snapshot:
     restart_count → ramp in last 30 % of history
     ready_ratio   → degradation in last 20 % of history
     event_count   → spike in last 15 % of history
   Useful for detecting clear unhealthy states when no TSDB is available,
   but the anomaly score is less informative than real data.
"""
from __future__ import annotations

import logging

import numpy as np

from ontology.entities import ResourceKind
from ontology.graph import OntologyGraph
from signals.patchtst_detector import AnomalyResult, PatchTSTDetector, SignalSegment

log = logging.getLogger(__name__)

_DEFAULT_HISTORY = 100


class SignalAnalyzer:
    """
    Runs PatchTST anomaly detection over K8s entity metrics and writes
    `signal.*` annotations back onto the entities.

    Parameters
    ----------
    detector:          PatchTSTDetector instance (default: standard config).
    history_length:    Points generated per synthetic signal.
    prometheus_source: Optional PrometheusMetricSource for real time series.
                       When provided, synthetic generation is skipped for any
                       entity/metric that returns data from Prometheus.
    """

    def __init__(
        self,
        detector: PatchTSTDetector | None = None,
        history_length: int = _DEFAULT_HISTORY,
        prometheus_source=None,   # PrometheusMetricSource | None
    ) -> None:
        self.detector = detector or PatchTSTDetector()
        self.history_length = history_length
        self.prometheus_source = prometheus_source

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def analyze(self, graph: OntologyGraph) -> list[AnomalyResult]:
        """
        Run signal analysis over every entity in the graph.

        Returns a flat list of AnomalyResult, one per (entity, metric, horizon).
        When a PrometheusMetricSource is available, real multi-horizon time series
        are used; otherwise synthetic history is generated from current state.
        Entities receive `signal.<metric>.<horizon>` annotations.
        """
        results: list[AnomalyResult] = []

        if self.prometheus_source is not None:
            horizon_segments = self._collect_real_segments(graph)
            for hs in horizon_segments:
                result = self.detector.detect(hs.segment)
                result.horizon = hs.horizon
                results.append(result)
                self._annotate(graph, result)
                log.info(
                    "signal[%s] %s/%s: %s (score=%.3f, method=%s)",
                    hs.horizon, result.entity_uid, result.metric_name,
                    result.severity, result.score, result.method,
                )
        else:
            for segment in self._collect_synthetic_segments(graph):
                result = self.detector.detect(segment)
                results.append(result)
                self._annotate(graph, result)
                log.info(
                    "signal[synthetic] %s/%s: %s (score=%.3f, method=%s)",
                    result.entity_uid, result.metric_name,
                    result.severity, result.score, result.method,
                )

        anomalous = [r for r in results if r.is_anomalous]
        mode = "real" if self.prometheus_source else "synthetic"
        log.info(
            "SignalAnalyzer[%s]: %d segments analysed, %d anomalous",
            mode, len(results), len(anomalous),
        )
        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Signal derivation
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_real_segments(self, graph: OntologyGraph):
        """Fetch multi-horizon segments from Prometheus for all relevant entities."""
        from signals.prometheus_source import HorizonSegment
        src = self.prometheus_source
        segments: list[HorizonSegment] = []

        for pod in graph.entities(ResourceKind.POD):
            if pod.namespace:
                fetched = src.pod_segments(pod.uid, pod.name, pod.namespace)
                segments.extend(fetched)
                if not fetched:
                    # Prometheus returned nothing — fall back to synthetic for this pod
                    segments.extend(self._synthetic_pod_segments(pod))

        for dep in graph.entities(ResourceKind.DEPLOYMENT):
            if dep.namespace and dep.replicas > 0:
                fetched = src.deployment_segments(dep.uid, dep.name, dep.namespace)
                segments.extend(fetched)
                if not fetched:
                    segments.extend(self._synthetic_deployment_segments(dep))

        for sts in graph.entities(ResourceKind.STATEFULSET):
            if sts.namespace and sts.replicas > 0:
                fetched = src.statefulset_segments(sts.uid, sts.name, sts.namespace)
                segments.extend(fetched)
                if not fetched:
                    segments.extend(self._synthetic_statefulset_segments(sts))

        for event in graph.entities(ResourceKind.EVENT):
            if event.is_warning and event.count > 1:
                segments.extend(self._synthetic_event_segments(event))

        return segments

    def _collect_synthetic_segments(self, graph: OntologyGraph) -> list[SignalSegment]:
        """Generate synthetic history from point-in-time K8s state."""
        segments: list[SignalSegment] = []
        rng = np.random.default_rng(42)

        for pod in graph.entities(ResourceKind.POD):
            if pod.restart_count >= 0:
                segments.append(SignalSegment(
                    entity_uid=pod.uid,
                    metric_name="restart_count",
                    values=_restart_signal(pod.restart_count, self.history_length, rng),
                ))

        for dep in graph.entities(ResourceKind.DEPLOYMENT):
            if dep.replicas > 0:
                segments.append(SignalSegment(
                    entity_uid=dep.uid,
                    metric_name="ready_ratio",
                    values=_readiness_signal(
                        dep.ready_replicas / dep.replicas, self.history_length, rng
                    ),
                ))

        for sts in graph.entities(ResourceKind.STATEFULSET):
            if sts.replicas > 0:
                segments.append(SignalSegment(
                    entity_uid=sts.uid,
                    metric_name="ready_ratio",
                    values=_readiness_signal(
                        sts.ready_replicas / sts.replicas, self.history_length, rng
                    ),
                ))

        for event in graph.entities(ResourceKind.EVENT):
            if event.is_warning and event.count > 1:
                segments.append(SignalSegment(
                    entity_uid=event.uid,
                    metric_name="event_count",
                    values=_event_signal(event.count, self.history_length, rng),
                ))

        return segments

    # ── Synthetic fallbacks for individual entities ───────────────────────────

    def _synthetic_pod_segments(self, pod):
        from signals.prometheus_source import HorizonSegment
        rng = np.random.default_rng(hash(pod.uid) & 0xFFFF)
        return [HorizonSegment(
            segment=SignalSegment(
                entity_uid=pod.uid,
                metric_name="restart_count",
                values=_restart_signal(pod.restart_count, self.history_length, rng),
            ),
            horizon="",
        )]

    def _synthetic_deployment_segments(self, dep):
        from signals.prometheus_source import HorizonSegment
        rng = np.random.default_rng(hash(dep.uid) & 0xFFFF)
        return [HorizonSegment(
            segment=SignalSegment(
                entity_uid=dep.uid,
                metric_name="ready_ratio",
                values=_readiness_signal(
                    dep.ready_replicas / dep.replicas, self.history_length, rng
                ),
            ),
            horizon="",
        )]

    def _synthetic_statefulset_segments(self, sts):
        from signals.prometheus_source import HorizonSegment
        rng = np.random.default_rng(hash(sts.uid) & 0xFFFF)
        return [HorizonSegment(
            segment=SignalSegment(
                entity_uid=sts.uid,
                metric_name="ready_ratio",
                values=_readiness_signal(
                    sts.ready_replicas / sts.replicas, self.history_length, rng
                ),
            ),
            horizon="",
        )]

    def _synthetic_event_segments(self, event):
        from signals.prometheus_source import HorizonSegment
        rng = np.random.default_rng(hash(event.uid) & 0xFFFF)
        return [HorizonSegment(
            segment=SignalSegment(
                entity_uid=event.uid,
                metric_name="event_count",
                values=_event_signal(event.count, self.history_length, rng),
            ),
            horizon="",
        )]

    # ─────────────────────────────────────────────────────────────────────────
    # Annotation helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _annotate(self, graph: OntologyGraph, result: AnomalyResult) -> None:
        entity = graph.get(result.entity_uid)
        if entity is None:
            return

        # signal.<metric>.<horizon> or signal.<metric> (synthetic)
        suffix = f".{result.horizon}" if result.horizon else ""
        ann_key = f"signal.{result.metric_name}{suffix}"
        entity.annotations[ann_key] = (
            f"metric={result.metric_name}"
            + (f" horizon={result.horizon}" if result.horizon else "")
            + f" severity={result.severity} score={result.score:.3f} method={result.method}"
        )

        # signal.anomaly = aggregate of all anomalous metrics/horizons
        if result.is_anomalous:
            existing = entity.annotations.get("signal.anomaly", "")
            entry = f"{result.metric_name}{suffix}={result.severity}"
            if entry not in existing:
                entity.annotations["signal.anomaly"] = (
                    (existing + " | " + entry) if existing else entry
                )


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic signal generators
# ─────────────────────────────────────────────────────────────────────────────

def _restart_signal(
    current_count: int,
    length: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Simulates restart count history.
    - First 70 % of history: stable near 0 (normal operation)
    - Last 30 %: ramps up to `current_count` (crash loop onset)
    """
    signal = np.zeros(length, dtype=np.float32)
    if current_count > 0:
        onset = int(length * 0.70)
        signal[onset:] = np.linspace(0.0, float(current_count), length - onset)
    noise = rng.normal(0.0, max(0.1, current_count * 0.02), length).astype(np.float32)
    return np.clip(signal + noise, 0, None)


def _readiness_signal(
    current_ratio: float,
    length: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Simulates deployment readiness ratio history.
    - First 80 % of history: stable at 1.0
    - Last 20 %: degrades to `current_ratio` (outage in progress)
    """
    signal = np.ones(length, dtype=np.float32)
    if current_ratio < 1.0:
        onset = int(length * 0.80)
        signal[onset:] = np.linspace(1.0, current_ratio, length - onset)
    noise = rng.normal(0.0, 0.02, length).astype(np.float32)
    return np.clip(signal + noise, 0.0, 1.0)


def _event_signal(
    current_count: int,
    length: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Simulates event frequency history.
    - First 85 % of history: near-zero baseline
    - Last 15 %: spike to `current_count`
    """
    signal = np.zeros(length, dtype=np.float32)
    signal += rng.exponential(0.5, length).astype(np.float32)
    onset = int(length * 0.85)
    signal[onset:] += np.linspace(0.0, float(current_count), length - onset)
    return np.clip(signal, 0, None)
