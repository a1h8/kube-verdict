"""Seeds are what is failing now — docs/fix-plan-001.md, H38: an Evicted pod its ReplicaSet
replaced was a seed, and a Deployment scaling up cleanly was "Unhealthy"."""
from __future__ import annotations

from dedup.bfs import find_unhealthy
from ontology.entities import Deployment, Pod
from ontology.graph import OntologyGraph

AVAILABLE = [{"type": "Available", "status": "True"}, {"type": "Progressing", "status": "True",
                                                      "reason": "NewReplicaSetAvailable"}]


def _seeds(*entities):
    g = OntologyGraph()
    for e in entities:
        g.add_entity(e)
    return {e.name for e in find_unhealthy(g)}


def _evicted(owner="ReplicaSet"):
    return Pod(uid="ev", name="api-7d9f-old", namespace="prod", phase="Failed",
               owner_ref_kind=owner, owner_ref_name="api-7d9f",
               raw={"status": {"phase": "Failed", "reason": "Evicted"}})


def test_a_replaced_evicted_pod_is_not_a_seed():
    assert _seeds(_evicted()) == set()


def test_a_failed_pod_without_a_replacing_controller_is():               # control
    bare = Pod(uid="b", name="once", namespace="prod", phase="Failed",
               raw={"status": {"phase": "Failed", "reason": "Error"}})
    assert _seeds(bare, _evicted(owner="Job")) == {"once", "api-7d9f-old"}


def _starting_pod(name):
    return Pod(uid=name, name=name, namespace="prod", phase="Running", labels={"app": "api"},
               container_statuses=[{"name": "c", "ready": False, "restartCount": 0,
                                    "state": {"running": {}}}])


def test_a_clean_scale_up_is_not_a_seed():
    dep = Deployment(uid="d", name="api", namespace="prod", replicas=8, ready_replicas=5,
                     available_replicas=5, selector={"app": "api"}, conditions=AVAILABLE)
    healthy = Pod(uid="h", name="api-1", namespace="prod", phase="Running", labels={"app": "api"},
                  container_statuses=[{"name": "c", "ready": True, "state": {"running": {}}}])
    assert "api" not in _seeds(dep, healthy)
    # control: the same Deployment with a crashing pod is a seed
    crashing = Pod(uid="x", name="api-2", namespace="prod", phase="Running", labels={"app": "api"},
                   container_statuses=[{"name": "c", "ready": False, "restartCount": 4,
                                        "state": {"waiting": {"reason": "CrashLoopBackOff"}}}])
    assert "api" in _seeds(dep, crashing)
