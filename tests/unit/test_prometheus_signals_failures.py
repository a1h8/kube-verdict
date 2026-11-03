"""The Prometheus signal source reports failures, keeps every series, and is bounded —
docs/fix-plan-001.md, H68."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import requests

import config as cfg
from ontology.entities import Pod
from ontology.graph import OntologyGraph
from signals import prometheus_source as ps
from signals.analyzer import SignalAnalyzer
from signals.patchtst_detector import AnomalyResult, PatchTSTDetector
from signals.prometheus_source import PrometheusMetricSource
from workflow import nodes


def _resp(*series):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json.return_value = {"data": {"result": [{"values": s} for s in series]}}
    return r


def _src():
    return PrometheusMetricSource(url="http://prom:9090", timeout=5)


@pytest.mark.parametrize("failure", [
    requests.Timeout(), requests.ConnectionError("refused"),
    requests.HTTPError("503 Service Unavailable"),
])
def test_a_failed_query_is_recorded(failure):
    src = _src()
    with patch("requests.get", side_effect=failure):
        assert src._range_query("up", 3600, 60) is None
    assert len(src.errors) == 1 and "up" in src.errors[0]


def test_an_empty_answer_is_not_a_failure():                                       # control
    src = _src()
    with patch("requests.get", return_value=_resp()):
        assert src._range_query("up", 3600, 60) is None
    assert src.errors == []


def test_a_container_started_later_does_not_drop_the_metric():
    app = [[100, "2"], [160, "2"], [220, "2"]]
    sidecar = [[160, "1"], [220, "1"]]                 # started in the window
    with patch("requests.get", return_value=_resp(app, sidecar)):
        values = _src()._range_query("mem", 3600, 60)
    np.testing.assert_allclose(values, [2.0, 3.0, 3.0])


def test_a_ratio_is_averaged():
    with patch("requests.get", return_value=_resp([[100, "1"]], [[100, "0.5"]])):
        np.testing.assert_allclose(_src()._range_query("r", 3600, 60, how="mean"), [0.75])


def test_the_pod_queries_leave_out_the_pod_cgroup_series():
    assert 'container!=""' in ps._Q_MEMORY and 'container!=""' in ps._Q_CPU


def _graph(n_pods=3):
    g = OntologyGraph()
    for i in range(n_pods):
        g.add_entity(Pod(uid=f"Pod/prod/p{i}", name=f"p{i}", namespace="prod", phase="Pending"))
    g.add_entity(Pod(uid="Pod/prod/ok", name="ok", namespace="prod", phase="Running"))
    return g


def test_the_analysis_is_limited_to_the_entities_asked():
    src = MagicMock(spec=PrometheusMetricSource)
    src.pod_segments.return_value = []
    SignalAnalyzer(prometheus_source=src, synthetic_fallback=False).analyze(
        _graph(), only={"Pod/prod/p0"})
    assert [c.args[0] for c in src.pod_segments.call_args_list] == ["Pod/prod/p0"]


def test_without_measured_history_nothing_is_trained():
    det = MagicMock(spec=PatchTSTDetector)
    results = SignalAnalyzer(detector=det, synthetic_fallback=False).analyze(_graph())
    assert results == [] and not det.detect.called
    # control: the UI's synthetic mode still runs
    det.detect.return_value = AnomalyResult("u", "m", "normal", 0.0, 100, "zscore")
    assert SignalAnalyzer(detector=det).analyze(_graph())


def _run_node(monkeypatch, graph, *, enabled, get):
    monkeypatch.setattr(cfg, "PROMETHEUS_ENABLED", enabled)
    monkeypatch.setattr(nodes, "_get_infra", lambda config, state: (graph, None))
    with patch("requests.get", side_effect=get), \
         patch.object(PatchTSTDetector, "detect", side_effect=AssertionError("trained")):
        return nodes.signal_analysis_node({}, {"configurable": {}})["ingestion_stats"]["signals"]


def test_the_node_trains_nothing_without_prometheus(monkeypatch):
    stats = _run_node(monkeypatch, _graph(), enabled=False, get=AssertionError("no HTTP"))
    assert stats["mode"] == "none" and stats["total"] == 0 and stats["fallback"] is False


def test_an_unreachable_prometheus_is_a_fallback(monkeypatch):
    stats = _run_node(monkeypatch, _graph(), enabled=True,
                      get=requests.ConnectionError("refused"))
    assert stats["fallback"] is True and "Prometheus queries failed" in stats["error"]


def test_the_seeds_beyond_the_budget_are_counted(monkeypatch):
    monkeypatch.setattr(cfg, "SIGNAL_MAX_ENTITIES", 2)
    stats = _run_node(monkeypatch, _graph(5), enabled=True, get=lambda *a, **k: _resp())
    assert stats["entities"] == 2 and stats["over_budget"] == 3
    assert stats["fallback"] is False                                             # empty ≠ failed
