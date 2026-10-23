"""A synthetic series is labelled and never evidence; a measured one still is —
docs/fix-plan-001.md, H43 (replayed on h003: 12 `SIGNAL=[… method=patchtst …]` in the prompt,
computed from the restart count itself, recorded as `mode: real`)."""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from ontology.entities import Pod
from ontology.graph import OntologyGraph
from signals.analyzer import SignalAnalyzer
from signals.patchtst_detector import AnomalyResult, PatchTSTDetector, SignalSegment
from signals.prometheus_source import HorizonSegment, PrometheusMetricSource


def _run(fetched):
    g = OntologyGraph()
    g.add_entity(Pod(uid="p1", name="api-0", namespace="prod", restart_count=216))
    src = MagicMock(spec=PrometheusMetricSource)
    src.pod_segments.return_value = fetched
    det = MagicMock(spec=PatchTSTDetector)
    det.detect.side_effect = lambda seg: AnomalyResult(
        entity_uid="p1", metric_name=seg.metric_name, severity="critical", score=216.0,
        n_points=60, method="patchtst")
    analyzer = SignalAnalyzer(detector=det, prometheus_source=src)
    analyzer.analyze(g)
    return analyzer, g.get("p1")


def test_prometheus_silent_for_a_pod_is_not_real_evidence():
    analyzer, pod = _run([])                     # source exists, answers nothing for this pod
    assert analyzer.last_mode == "synthetic"
    assert not any(k.startswith("signal.") for k in pod.annotations)


def test_a_measured_series_is_still_evidence():                           # control
    seg = SignalSegment(entity_uid="p1", metric_name="restart_count", values=np.ones(60))
    analyzer, pod = _run([HorizonSegment(segment=seg, horizon="short")])
    assert analyzer.last_mode == "real"
    assert "signal.restart_count.short" in pod.annotations
