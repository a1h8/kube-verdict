"""Rule checks contract — docs/diagnostics.md § Step 1, Safety nets.

Every RemediationEngine rule declares read-only checks (``checks()``) apart from
its actions (``commands()``): a check is never an action, an action is never a
check, and every check says what it verifies and what confirms the hypothesis.
"""
from __future__ import annotations

import pytest

from ontology.entities import Deployment, K8sEvent, Pod, ResourceKind  # noqa: F401
from ontology.graph import OntologyGraph
from rca.diagnostics import is_read_only
from rca.remediation_engine import _ALL_RULES, RemediationEngine


# the old engine's fixture graph (moved from test_analyzer_fallback.py with the LOW fallback)
def _demo_graph() -> OntologyGraph:
    from ontology.entities import K8sEvent
    g = OntologyGraph()

    # payment-service → CrashLoopBackOff + DB connection refused
    crash = Pod(uid="p1", name="payment-service-0", namespace="demo",
                phase="CrashLoopBackOff", restart_count=8)
    ev_db = K8sEvent(uid="ev1", name="ev1", namespace="demo",
                     event_type="Warning", involved_name="payment-service-0",
                     reason="BackOff", message="connection refused to db:5432")

    # analytics-worker → OOMKilled with memory drift
    oom = Pod(uid="p2", name="analytics-worker-0", namespace="demo", phase="Error")
    oom.container_statuses = [
        {"lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}
    ]
    oom.annotations["drift.resources.limits.memory"] = "declared=512Mi observed=50Mi"

    # notification-service → missing ConfigMap
    notif = Pod(uid="p3", name="notification-service-0", namespace="demo", phase="Pending")
    ev_cfg = K8sEvent(uid="ev2", name="ev2", namespace="demo",
                      event_type="Warning", involved_name="notification-service-0",
                      reason="Failed", message="configmap notification-config not found")

    # ml-inference → ImagePullBackOff with image drift
    ml = Pod(uid="p4", name="ml-inference-0", namespace="demo", phase="ImagePullBackOff")
    ml.annotations["drift.image.tag"] = "declared=nginx:1.25 observed=private.registry.io/ml:broken"

    # gpu-worker → Pending / Unschedulable
    gpu = Pod(uid="p5", name="gpu-worker-0", namespace="demo", phase="Pending")
    ev_sched = K8sEvent(uid="ev3", name="ev3", namespace="demo",
                        event_type="Warning", involved_name="gpu-worker-0",
                        reason="FailedScheduling",
                        message="0/1 nodes available: Unschedulable nodeSelector gpu=true")

    for e in [crash, ev_db, oom, notif, ev_cfg, ml, gpu, ev_sched]:
        g.add_entity(e)
    return g

def _graph():
    g = _demo_graph()
    g.add_entity(Deployment(uid="d1", name="checkout", namespace="demo",
                            replicas=3, ready_replicas=0))
    return g


@pytest.fixture(scope="module")
def hypotheses():
    return RemediationEngine().score(_graph())


def test_every_rule_fires_on_the_fixture_graph(hypotheses):
    fired = {h.rule_id for h in hypotheses}
    assert fired == {r.id for r in _ALL_RULES}


def test_every_rule_declares_checks(hypotheses):
    for h in hypotheses:
        assert h.checks, h.rule_id


def test_checks_are_read_only_and_explained(hypotheses):
    for h in hypotheses:
        for check in h.checks:
            assert is_read_only(check.command), (h.rule_id, check.command)
            assert check.purpose.strip(), (h.rule_id, check.command)
            assert check.confirms_if.strip(), (h.rule_id, check.command)


def test_commands_are_actions_only(hypotheses):
    for h in hypotheses:
        for cmd in h.commands:
            if cmd.strip().startswith("#"):
                continue
            assert not is_read_only(cmd), (h.rule_id, cmd)
