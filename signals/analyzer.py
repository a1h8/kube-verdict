"""
SignalAnalyzer — integrates PatchTST anomaly detection with the OntologyGraph.

Derives time series signals from entity attributes (restart counts, ready
ratios, event frequency) and annotates entities with `signal.*` annotations.
These annotations surface in entity.to_text() and are included in the context
window under the [SIGNALS] section.
"""
from __future__ import annotations

import logging

import numpy as np

from ontology.entities import ResourceKind
from ontology.graph import OntologyGraph
from signals.patchtst_detector import AnomalyResult, PatchTSTDetector, SignalSegment

log = logging.getLogger(__name__)

# Minimum number of synthetic history points generated per entity.
_DEFAULT_HISTORY = 100


class SignalAnalyzer:
    """
    Runs PatchTST anomaly detection over K8s entity metrics derived from the
    OntologyGraph, then writes `signal.*` annotations back onto the entities.

    Metric derivation
    -----------------
    Since KubeWhisperer collects point-in-time snapshots rather than
    time-series databases, synthetic history is generated around the current
    observed value:

    - **restart_count** (Pod):
      Simulate a gradual increase toward the current restart count in the
      last 30 % of history — if restart_count > 0 this mimics a crash loop.

    - **ready_ratio** (Deployment / StatefulSet / DaemonSet):
      Simulate stable-at-1.0 history degrading in the last 20 % to the
      current ratio — mirrors an outage in progress.

    - **event_count** (Warning events per entity):
      Simulate stable low-count history with a spike at the end to the
      current event count.

    In production, replace these generators with real metrics-server or
    Prometheus queries for genuine time series.
    """

    def __init__(
        self,
        detector: PatchTSTDetector | None = None,
        history_length: int = _DEFAULT_HISTORY,
    ) -> None:
        self.detector = detector or PatchTSTDetector()
        self.history_length = history_length

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def analyze(self, graph: OntologyGraph) -> list[AnomalyResult]:
        """
        Run signal analysis over every entity in the graph.

        Returns a flat list of AnomalyResult, one per (entity, metric).
        Entities receive `signal.<metric>` annotations; severity != "normal"
        also annotates with `signal.anomaly` for quick filtering.
        """
        results: list[AnomalyResult] = []

        for segment in self._collect_segments(graph):
            result = self.detector.detect(segment)
            results.append(result)
            self._annotate(graph, result)
            log.info(
                "signal %s/%s: %s (score=%.3f, method=%s)",
                result.entity_uid, result.metric_name,
                result.severity, result.score, result.method,
            )

        anomalous = [r for r in results if r.is_anomalous]
        log.info(
            "SignalAnalyzer: %d segments analysed, %d anomalous",
            len(results), len(anomalous),
        )
        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Signal derivation
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_segments(self, graph: OntologyGraph) -> list[SignalSegment]:
        segments: list[SignalSegment] = []
        rng = np.random.default_rng(42)

        # Pods → restart_count signal
        for pod in graph.entities(ResourceKind.POD):
            if pod.restart_count >= 0:
                segments.append(SignalSegment(
                    entity_uid=pod.uid,
                    metric_name="restart_count",
                    values=_restart_signal(pod.restart_count, self.history_length, rng),
                ))

        # Deployments → ready_ratio signal
        for dep in graph.entities(ResourceKind.DEPLOYMENT):
            if dep.replicas > 0:
                ratio = dep.ready_replicas / dep.replicas
                segments.append(SignalSegment(
                    entity_uid=dep.uid,
                    metric_name="ready_ratio",
                    values=_readiness_signal(ratio, self.history_length, rng),
                ))

        # StatefulSets → ready_ratio signal
        for sts in graph.entities(ResourceKind.STATEFULSET):
            if sts.replicas > 0:
                ratio = sts.ready_replicas / sts.replicas
                segments.append(SignalSegment(
                    entity_uid=sts.uid,
                    metric_name="ready_ratio",
                    values=_readiness_signal(ratio, self.history_length, rng),
                ))

        # Warning events → event_count spike signal
        for event in graph.entities(ResourceKind.EVENT):
            if event.is_warning and event.count > 1:
                segments.append(SignalSegment(
                    entity_uid=event.uid,
                    metric_name="event_count",
                    values=_event_signal(event.count, self.history_length, rng),
                ))

        return segments

    # ─────────────────────────────────────────────────────────────────────────
    # Annotation helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _annotate(self, graph: OntologyGraph, result: AnomalyResult) -> None:
        entity = graph.get(result.entity_uid)
        if entity is None:
            return

        # signal.<metric> = human-readable summary
        entity.annotations[f"signal.{result.metric_name}"] = (
            f"metric={result.metric_name} severity={result.severity} "
            f"score={result.score:.3f} method={result.method}"
        )

        # signal.anomaly = set if any metric is anomalous
        if result.is_anomalous:
            existing = entity.annotations.get("signal.anomaly", "")
            entry = f"{result.metric_name}={result.severity}"
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
