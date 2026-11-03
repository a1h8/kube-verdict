"""A firing alert that names no workload is kept; one of another namespace is counted —
docs/fix-plan-001.md, H33 (cluster alerts never reached the analysis)."""
from __future__ import annotations

from unittest.mock import patch

from ingestion.prometheus_collector import PrometheusCollector
from ontology.entities import Namespace, Pod, ResourceKind
from ontology.graph import OntologyGraph
from rca.signals import extract_signals


def _alert(name, summary, **labels):
    return {"state": "firing", "labels": {"alertname": name, "severity": "critical", **labels},
            "annotations": {"summary": summary}, "activeAt": "2026-10-18T10:00:00Z"}


ALERTS = [
    _alert("APIServerHighErrorRate", "apiserver 5xx above 5%"),                        # cluster
    _alert("OrdersApiHighErrorRate", "error rate 12%", namespace="staging", deployment="orders-api"),
    _alert("PodCrash", "pod crashing", namespace="prod", pod="api-1"),                 # matched
]


def _collect():
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns", name="prod"))
    g.add_entity(Pod(uid="p", name="api-1", namespace="prod", phase="Running"))
    c = PrometheusCollector(url="http://prom")
    with patch.object(PrometheusCollector, "_fetch_alerts", return_value=ALERTS):
        n = c.collect(g)
    return g, c, n


def test_a_cluster_alert_is_kept_and_another_namespace_is_counted():
    g, c, n = _collect()
    names = {a.alert_name for a in g.entities(ResourceKind.PROMETHEUS_ALERT)}
    assert "APIServerHighErrorRate" in names
    assert "OrdersApiHighErrorRate" not in names                      # control: scope still holds
    assert (n, c.uncorrelated, c.out_of_scope) == (1, 1, 1)


def test_the_cluster_alert_becomes_a_signal_about_itself():
    g, _, _ = _collect()
    ss = extract_signals(g)
    sig = next(s for s in ss.signals if s.kind == "app.error_rate")
    assert ss.subject(sig) == "PrometheusAlert/APIServerHighErrorRate"
