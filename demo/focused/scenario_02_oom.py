"""
Scenario 02 — OOMKilled + memory limit drift
Single incident: analytics-worker deployed with 50Mi, Helm chart declares 256Mi
Fix: kubectl patch memory limit → pod stable
"""
from __future__ import annotations
from ontology.entities import Deployment, K8sEvent as Event, Namespace, Pod
from ontology.graph import OntologyGraph

NS = "kubeverdict-demo"

TITLE  = "OOMKilled — memory limit drift"
QUERY  = (
    "analytics-worker is OOMKilled with 12 restarts. "
    "The deployed memory limit is 50Mi but the Helm chart declares 256Mi. "
    "Identify the drift and provide the remediation command."
)

INCIDENTS = [
    {
        "service":  "analytics-worker",
        "status":   "OOMKilled",
        "restarts": 12,
        "cause":    "Memory limit drift — deployed 50Mi, Helm chart 256Mi (undeployed)",
        "severity": "high",
    },
]

HEALED_INCIDENTS = [
    {"service": "analytics-worker", "status": "Running", "restarts": 0, "cause": "—", "severity": "ok"},
]


def build_graph() -> OntologyGraph:
    graph = OntologyGraph(server_version="v1.31.4")
    graph.add_entity(Namespace(uid=f"ns/{NS}", name=NS, namespace=NS))

    pod = Pod(uid=f"pod/{NS}/analytics-worker", name="analytics-worker", namespace=NS)
    pod.phase = "OOMKilled"
    pod.restart_count = 12
    pod.annotations = {
        "anchor.spec.containers[0].resources.limits.memory":
            "256Mi [manifest] vs 50Mi [observed]",
        "signal.oom": "Container worker OOM killed — limit 50Mi deployed, allocated 200Mi before kill",
    }
    graph.add_entity(pod)

    deploy = Deployment(uid=f"deploy/{NS}/analytics-worker", name="analytics-worker", namespace=NS)
    deploy.replicas = 1
    deploy.ready_replicas = 0
    graph.add_entity(deploy)

    ev = Event(uid=f"event/{NS}/analytics-oom", name="analytics-oom", namespace=NS)
    ev.event_type = "Warning"
    ev.reason = "OOMKilling"
    ev.message = "Memory cgroup out of memory: container worker allocated 200Mi, enforced limit 50Mi"
    ev.involved_name = "analytics-worker"
    ev.involved_kind = "Pod"
    graph.add_entity(ev)

    return graph
