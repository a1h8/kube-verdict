"""
Demo scenario — fake K8s cluster, no real cluster needed.

Builds an OntologyGraph with 5 services and 4 injected incidents
that the RCA workflow will analyse:

  payment-api        CrashLoopBackOff  DB unreachable (missing endpoints)
  analytics-worker   OOMKilled         memory limit 50Mi vs 200Mi actual
  notification-svc   ImagePullBackOff  image tag drift (manifest vs observed)
  ml-inference       Pending           resource quota exceeded
  api-gateway        Running           healthy baseline
"""
from __future__ import annotations

from ontology.entities import (
    Deployment, Event, Namespace, Pod, Service,
)
from ontology.graph import OntologyGraph

NS = "kubewhisperer-demo"
KUBE_VERSION = "1.29.4"


def build_graph() -> OntologyGraph:
    from ontology.entities import KubeVersion
    graph = OntologyGraph(server_version=KubeVersion(1, 29, 4))

    ns = Namespace(name=NS, namespace=NS)
    ns.uid = f"ns/{NS}"
    graph.add_entity(ns)

    _add_payment_api(graph)
    _add_analytics_worker(graph)
    _add_notification_svc(graph)
    _add_ml_inference(graph)
    _add_api_gateway(graph)

    return graph


# ── Incident 1: payment-api — CrashLoopBackOff, DB unreachable ───────────────

def _add_payment_api(graph: OntologyGraph) -> None:
    deploy = Deployment(name="payment-api", namespace=NS)
    deploy.uid = f"deploy/{NS}/payment-api"
    deploy.replicas = 3
    deploy.ready_replicas = 0
    deploy.annotations = {
        "anchor.spec.replicas": "3 [manifest] vs 0 [observed]",
    }
    graph.add_entity(deploy)

    for i in range(3):
        pod = Pod(name=f"payment-api-{i}", namespace=NS)
        pod.uid = f"pod/{NS}/payment-api-{i}"
        pod.phase = "CrashLoopBackOff"
        pod.restart_count = 47 + i
        pod.owner_ref_kind = "Deployment"
        pod.owner_ref_name = "payment-api"
        pod.annotations = {
            "signal.exit_code": "1",
            "signal.last_log":  "FATAL: database initialisation failed — connect: connection refused",
        }
        graph.add_entity(pod)

    # DB service exists but has no endpoints — the missing dependency
    svc = Service(name="db-primary", namespace=NS)
    svc.uid = f"svc/{NS}/db-primary"
    svc.annotations = {"missing.endpoints": "Service db-primary has no ready endpoints"}
    graph.add_entity(svc)

    event = Event(name="payment-api-crashloop", namespace=NS)
    event.uid = f"event/{NS}/payment-api-crashloop"
    event.event_type = "Warning"
    event.reason = "BackOff"
    event.message = "Back-off restarting failed container payment in pod payment-api-0"
    event.involved_object = "payment-api-0"
    graph.add_entity(event)

    event2 = Event(name="payment-api-db-refused", namespace=NS)
    event2.uid = f"event/{NS}/payment-api-db-refused"
    event2.event_type = "Warning"
    event2.reason = "Failed"
    event2.message = "dial tcp 10.96.5.22:5432: connect: connection refused"
    event2.involved_object = "payment-api-0"
    graph.add_entity(event2)


# ── Incident 2: analytics-worker — OOMKilled, memory limit drift ──────────────

def _add_analytics_worker(graph: OntologyGraph) -> None:
    pod = Pod(name="analytics-worker", namespace=NS)
    pod.uid = f"pod/{NS}/analytics-worker"
    pod.phase = "OOMKilled"
    pod.restart_count = 12
    pod.annotations = {
        "anchor.spec.containers[0].resources.limits.memory":
            "50Mi [manifest] vs 200Mi [observed allocation before kill]",
        "signal.oom": "Container worker exceeded memory limit and was killed by OOM killer",
    }
    graph.add_entity(pod)

    event = Event(name="analytics-oom", namespace=NS)
    event.uid = f"event/{NS}/analytics-oom"
    event.event_type = "Warning"
    event.reason = "OOMKilling"
    event.message = "Memory cgroup out of memory: container worker used 200Mi, limit 50Mi"
    event.involved_object = "analytics-worker"
    graph.add_entity(event)


# ── Incident 3: notification-svc — ImagePullBackOff, tag drift ───────────────

def _add_notification_svc(graph: OntologyGraph) -> None:
    deploy = Deployment(name="notification-svc", namespace=NS)
    deploy.uid = f"deploy/{NS}/notification-svc"
    deploy.replicas = 2
    deploy.ready_replicas = 0
    deploy.annotations = {
        "anchor.spec.containers[0].image":
            "myregistry.io/notification:v3.2.1 [manifest] vs myregistry.io/notification:latest [observed]",
        "drift.image": "Helm declared myregistry.io/notification:v3.2.1 — cluster running :latest",
    }
    graph.add_entity(deploy)

    pod = Pod(name="notification-svc-0", namespace=NS)
    pod.uid = f"pod/{NS}/notification-svc-0"
    pod.phase = "ImagePullBackOff"
    pod.restart_count = 0
    pod.owner_ref_kind = "Deployment"
    pod.owner_ref_name = "notification-svc"
    graph.add_entity(pod)

    event = Event(name="notification-imagepull", namespace=NS)
    event.uid = f"event/{NS}/notification-imagepull"
    event.event_type = "Warning"
    event.reason = "Failed"
    event.message = (
        "Failed to pull image myregistry.io/notification:latest: "
        "rpc error: code = NotFound: image not found"
    )
    event.involved_object = "notification-svc-0"
    graph.add_entity(event)


# ── Incident 4: ml-inference — Pending, resource quota exceeded ───────────────

def _add_ml_inference(graph: OntologyGraph) -> None:
    deploy = Deployment(name="ml-inference", namespace=NS)
    deploy.uid = f"deploy/{NS}/ml-inference"
    deploy.replicas = 1
    deploy.ready_replicas = 0
    graph.add_entity(deploy)

    pod = Pod(name="ml-inference-0", namespace=NS)
    pod.uid = f"pod/{NS}/ml-inference-0"
    pod.phase = "Pending"
    pod.restart_count = 0
    pod.owner_ref_kind = "Deployment"
    pod.owner_ref_name = "ml-inference"
    pod.annotations = {
        "signal.unschedulable": "0/1 nodes are available: Insufficient nvidia.com/gpu",
    }
    graph.add_entity(pod)

    event = Event(name="ml-inference-pending", namespace=NS)
    event.uid = f"event/{NS}/ml-inference-pending"
    event.event_type = "Warning"
    event.reason = "FailedScheduling"
    event.message = "0/1 nodes are available: 1 Insufficient nvidia.com/gpu"
    event.involved_object = "ml-inference-0"
    graph.add_entity(event)


# ── Baseline: api-gateway — healthy ──────────────────────────────────────────

def _add_api_gateway(graph: OntologyGraph) -> None:
    deploy = Deployment(name="api-gateway", namespace=NS)
    deploy.uid = f"deploy/{NS}/api-gateway"
    deploy.replicas = 2
    deploy.ready_replicas = 2
    graph.add_entity(deploy)

    for i in range(2):
        pod = Pod(name=f"api-gateway-{i}", namespace=NS)
        pod.uid = f"pod/{NS}/api-gateway-{i}"
        pod.phase = "Running"
        pod.restart_count = 0
        pod.owner_ref_kind = "Deployment"
        pod.owner_ref_name = "api-gateway"
        graph.add_entity(pod)

    svc = Service(name="api-gateway", namespace=NS)
    svc.uid = f"svc/{NS}/api-gateway"
    graph.add_entity(svc)


# ── Cluster summary ───────────────────────────────────────────────────────────

INCIDENTS = [
    {
        "service":  "payment-api",
        "status":   "CrashLoopBackOff",
        "restarts": 47,
        "cause":    "DB unreachable — db-primary service has no endpoints",
        "severity": "critical",
    },
    {
        "service":  "analytics-worker",
        "status":   "OOMKilled",
        "restarts": 12,
        "cause":    "Memory limit 50Mi — container needs 200Mi",
        "severity": "high",
    },
    {
        "service":  "notification-svc",
        "status":   "ImagePullBackOff",
        "restarts": 0,
        "cause":    "Image tag drift — manifest v3.2.1, cluster :latest not found",
        "severity": "high",
    },
    {
        "service":  "ml-inference",
        "status":   "Pending",
        "restarts": 0,
        "cause":    "No schedulable node — GPU resource unavailable",
        "severity": "medium",
    },
    {
        "service":  "api-gateway",
        "status":   "Running",
        "restarts": 0,
        "cause":    "—",
        "severity": "ok",
    },
]
