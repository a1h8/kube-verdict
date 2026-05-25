"""
Scenario 01 — ImagePullBackOff + Helm image tag drift
Single incident: notification-svc running :latest (removed) vs chart v3.2.1
Fix: kubectl set image → Running
"""
from __future__ import annotations
from ontology.entities import Deployment, K8sEvent as Event, Namespace, Pod, Service
from ontology.graph import OntologyGraph

NS = "kubeverdict-demo"

TITLE  = "ImagePullBackOff — Helm image tag drift"
QUERY  = (
    "notification-svc is in ImagePullBackOff. "
    "The Helm chart declares image v3.2.1 but the cluster is running :latest. "
    "Identify the root cause and provide the exact remediation command."
)

INCIDENTS = [
    {
        "service":  "notification-svc",
        "status":   "ImagePullBackOff",
        "restarts": 0,
        "cause":    "Image tag drift — manifest v3.2.1, cluster :latest (removed from registry)",
        "severity": "high",
    },
]

HEALED_INCIDENTS = [
    {"service": "notification-svc", "status": "Running", "restarts": 0, "cause": "—", "severity": "ok"},
]


def build_graph() -> OntologyGraph:
    graph = OntologyGraph(server_version="v1.31.4")
    graph.add_entity(Namespace(uid=f"ns/{NS}", name=NS, namespace=NS))

    deploy = Deployment(uid=f"deploy/{NS}/notification-svc", name="notification-svc", namespace=NS)
    deploy.replicas = 2
    deploy.ready_replicas = 0
    deploy.annotations = {
        "anchor.spec.containers[0].image":
            "myregistry.io/notification:v3.2.1 [manifest] vs myregistry.io/notification:latest [observed]",
        "drift.image": "Helm declared myregistry.io/notification:v3.2.1 — cluster running :latest",
    }
    graph.add_entity(deploy)

    pod = Pod(uid=f"pod/{NS}/notification-svc-0", name="notification-svc-0", namespace=NS)
    pod.phase = "ImagePullBackOff"
    pod.restart_count = 0
    pod.owner_ref_kind = "Deployment"
    pod.owner_ref_name = "notification-svc"
    graph.add_entity(pod)

    svc = Service(uid=f"svc/{NS}/notification-svc", name="notification-svc", namespace=NS)
    graph.add_entity(svc)

    ev = Event(uid=f"event/{NS}/notif-imagepull", name="notif-imagepull", namespace=NS)
    ev.event_type = "Warning"
    ev.reason = "Failed"
    ev.message = (
        "Failed to pull image myregistry.io/notification:latest: "
        "rpc error: code = NotFound desc = image not found in registry"
    )
    ev.involved_name = "notification-svc-0"
    ev.involved_kind = "Pod"
    graph.add_entity(ev)

    return graph
