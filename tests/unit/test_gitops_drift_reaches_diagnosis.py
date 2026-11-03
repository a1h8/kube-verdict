"""The render-vs-live drift (`gitops.*`) reaches the signals, the diagnosis and the prompt —
docs/fix-plan-001.md, H28 (proved: "512Mi" absent from the prompt, diagnosis generic oom.limit)."""
from __future__ import annotations

from ontology.entities import Deployment, Pod
from ontology.graph import OntologyGraph
from rca.context_builder import ContextBuilder
from rca.diagnosis import diagnose
from rca.signals import extract_signals

DRIFT = ("drift field=spec.template.spec.containers[api].resources.limits.memory "
         "declared=512Mi observed=128Mi severity=critical")


def _graph(with_drift=True):
    g = OntologyGraph()
    dep = Deployment(uid="d", name="api", namespace="prod", replicas=1, selector={"app": "api"})
    if with_drift:
        dep.annotations["gitops.spec.template.spec.containers[api].resources.limits.memory"] = DRIFT
        dep.annotations["gitops.drift_count"] = "1"
    g.add_entity(dep)
    g.add_entity(Pod(uid="p", name="api-7d9f-x", namespace="prod", phase="Running", restart_count=3,
                     labels={"app": "api"},
                     container_statuses=[{"name": "api", "ready": False, "restartCount": 3,
                                          "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                          "lastState": {"terminated": {"reason": "OOMKilled"}}}]))
    return g


def test_the_gitops_drift_is_a_signal_and_explains_the_oom():
    ss = extract_signals(_graph())
    drift = [s for s in ss.signals if s.kind == "drift.config"]
    assert len(drift) == 1 and drift[0].source == "gitops"          # gitops.drift_count is not one
    top = diagnose(ss).diagnoses[0]
    assert top.rule == "oom.memory_drift" and "512Mi" in top.cause and "128Mi" in top.cause


def test_without_the_drift_the_oom_stays_generic():                    # control
    top = diagnose(extract_signals(_graph(with_drift=False))).diagnoses
    assert not top or top[0].rule != "oom.memory_drift"


def test_the_gitops_drift_is_in_the_prompt_drift_section():
    from unittest.mock import MagicMock
    store = MagicMock()
    store.hybrid_search.return_value = []
    store.search.return_value = []
    ctx = ContextBuilder(_graph(), store).build("why is api crashing")
    assert any("512Mi" in d and "[deployed ≠ Git]" in d for d in ctx.drift)
