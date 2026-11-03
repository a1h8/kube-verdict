"""An SLO burnt by a slow upstream — docs/failing-cases-002.md S5 (h013)."""
from __future__ import annotations

from ontology.entities import Deployment, Pod, PrometheusAlert
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType as R
from rca.diagnosis import diagnose
from rca.signals import extract_signals


def _alert(uid, name, **labels):
    return PrometheusAlert(uid=uid, name=name, namespace="prod", alert_name=name, severity="warning",
                           state="firing", summary=name, alert_labels={"deployment": "checkout", **labels})


def _outcome(upstream=True, pod_ready=True):
    g = OntologyGraph()
    g.add_entity(Deployment(uid="d", name="checkout", namespace="prod", replicas=3, selector={"app": "checkout"}))
    g.add_entity(Pod(uid="p", name="checkout-7d9f-x", namespace="prod", phase="Running", labels={"app": "checkout"},
                     container_statuses=[{"name": "c", "ready": pod_ready, "restartCount": 0 if pod_ready else 5,
                                          "state": {"running": {}} if pod_ready else
                                          {"waiting": {"reason": "CrashLoopBackOff"}}}]))
    g.add_entity(_alert("a1", "SLOErrorBudgetBurnFast"))
    g.add_edge(Edge("d", "a1", R.HAS_ALERT))
    if upstream:
        g.add_entity(_alert("a2", "UpstreamLatencyHigh", upstream="payments-gateway"))
        g.add_edge(Edge("d", "a2", R.HAS_ALERT))
    return diagnose(extract_signals(g))


def test_a_slow_upstream_burns_the_slo():
    d = _outcome().diagnoses[0]
    assert (d.rule, d.cause_location) == ("slo.dependency", "outside:payments-gateway")


def test_an_slo_burn_without_an_upstream_signal_stays_unexplained():                 # control
    assert not any(d.rule == "slo.dependency" for d in _outcome(upstream=False).diagnoses)


def test_failing_pods_are_not_blamed_on_the_upstream():                              # control
    assert not any(d.rule == "slo.dependency" for d in _outcome(pod_ready=False).diagnoses)
