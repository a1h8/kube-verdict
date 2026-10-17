"""Seed selection — docs/reasoning-path-tests.md P1, P2 and docs/test-cases.md § h026."""
from __future__ import annotations

import pytest

from dedup.bfs import find_unhealthy
from ontology.entities import Deployment, Pod
from ontology.graph import OntologyGraph

PROGRESSING = {"type": "Progressing", "status": "True", "reason": "ReplicaSetUpdated"}
STALLED = {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}
DONE = {"type": "Progressing", "status": "True", "reason": "NewReplicaSetAvailable"}


def _pod(name: str, *, phase="Running", ready=True, restarts=0, state=None) -> Pod:
    state = state or {"running": {"startedAt": "2026-10-17T10:00:05Z"}}
    return Pod(uid=f"pod-{name}", name=name, namespace="production", phase=phase,
               restart_count=restarts, owner_ref_kind="ReplicaSet", owner_ref_name="api-6c8b7f9d4",
               container_statuses=[{"name": "api", "ready": ready, "restartCount": restarts,
                                    "state": state}])


def _seeds(*entities) -> set[str]:
    g = OntologyGraph()
    for e in entities:
        g.add_entity(e)
    return {e.name for e in find_unhealthy(g)}


AVAILABLE = {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"}
UNAVAILABLE = {"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"}


def _deployment(*conditions) -> Deployment:
    return Deployment(uid="dep-api", name="api", namespace="production", replicas=3,
                      ready_replicas=3, available_replicas=3, conditions=list(conditions))


def test_running_but_not_ready_pod_is_a_seed():                      # P1
    assert _seeds(_pod("api-1", ready=False)) == {"api-1"}


def test_a_past_restart_alone_is_not_a_seed():                        # P2
    assert _seeds(_pod("api-1", restarts=1)) == set()


def test_a_pod_that_is_not_running_is_a_seed():
    assert _seeds(_pod("api-1", phase="Pending")) == {"api-1"}


@pytest.mark.parametrize("condition,seeded", [
    (PROGRESSING, False),   # a rollout in progress: the new pod is starting
    (STALLED, True),        # the rollout stalled past its deadline: an incident again
    (DONE, True),           # the rollout is over: a not-ready pod is a problem
])
def test_not_ready_pod_during_a_rollout(condition, seeded):
    names = _seeds(_deployment(AVAILABLE, condition), _pod("api-1", ready=False))
    assert ("api-1" in names) is seeded


def test_a_rollout_does_not_hide_a_pod_that_is_not_running():
    assert "api-1" in _seeds(_deployment(PROGRESSING), _pod("api-1", phase="Failed"))


def test_a_rollout_does_not_hide_a_crashing_pod():
    """Progressing stays True until the deadline even when the new pods crash (h003)."""
    crashing = _pod("api-1", ready=False, restarts=3,
                    state={"waiting": {"reason": "CrashLoopBackOff"}})
    assert "api-1" in _seeds(_deployment(AVAILABLE, PROGRESSING), crashing)


def test_a_rollout_that_lost_availability_does_not_hide_its_pods():
    assert "api-1" in _seeds(_deployment(UNAVAILABLE, PROGRESSING), _pod("api-1", ready=False))
