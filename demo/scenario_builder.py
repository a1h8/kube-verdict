"""
Demo scenario — fake K8s cluster, no real cluster needed.

Builds an OntologyGraph with 5 services and 4 injected incidents
that the RCA workflow will analyse:

  payment-api        CrashLoopBackOff  DB unreachable — db-primary scaled to 0 (Helm drift)
  analytics-worker   OOMKilled         memory limit 50Mi deployed, chart declares 256Mi
  notification-svc   ImagePullBackOff  image tag drift (manifest v3.2.1 vs deployed :latest)
  ml-inference       Pending           no GPU node schedulable
  api-gateway        Running           healthy baseline
"""
from __future__ import annotations

from ontology.entities import (   # direct submodule — avoids ontology/__init__ kubernetes import
    Deployment, K8sEvent as Event, Namespace, Pod, Service,
)
from ontology.graph import OntologyGraph  # noqa: E402

NS = "kubeverdict-demo"
KUBE_VERSION = "1.29.4"


def build_graph() -> OntologyGraph:
    graph = OntologyGraph(server_version="v1.31.4")

    ns = Namespace(uid=f"ns/{NS}", name=NS, namespace=NS)
    graph.add_entity(ns)

    _add_payment_api(graph)
    _add_analytics_worker(graph)
    _add_notification_svc(graph)
    _add_ml_inference(graph)
    _add_api_gateway(graph)

    return graph


# ── Incident 1: payment-api — CrashLoopBackOff, db-primary scaled to 0 ────────

def _add_payment_api(graph: OntologyGraph) -> None:
    # payment-api itself — symptom only, no anchor (replicas=0 is a cascade, not root cause)
    deploy = Deployment(uid=f"deploy/{NS}/payment-api", name="payment-api", namespace=NS)
    deploy.replicas = 3
    deploy.ready_replicas = 0
    graph.add_entity(deploy)

    for i in range(3):
        pod = Pod(uid=f"pod/{NS}/payment-api-{i}", name=f"payment-api-{i}", namespace=NS)
        pod.phase = "CrashLoopBackOff"
        pod.restart_count = 47 + i
        pod.owner_ref_kind = "Deployment"
        pod.owner_ref_name = "payment-api"
        pod.annotations = {
            "signal.exit_code": "1",
            "signal.last_log":  "FATAL: database initialisation failed — connect: connection refused",
        }
        graph.add_entity(pod)

    # Root cause: db-primary scaled to 0 (Helm chart declares replicas=1, deployed=0)
    db = Deployment(uid=f"deploy/{NS}/db-primary", name="db-primary", namespace=NS)
    db.replicas = 1
    db.ready_replicas = 0
    db.annotations = {
        "anchor.spec.replicas": "1 [manifest] vs 0 [observed]",
        "drift.replicas": "Helm declared replicas=1 — cluster running replicas=0 (scaled down manually)",
    }
    graph.add_entity(db)

    svc = Service(uid=f"svc/{NS}/db-primary", name="db-primary", namespace=NS)
    svc.annotations = {"missing.endpoints": "Service db-primary has 0 ready endpoints — no backing pod"}
    graph.add_entity(svc)

    event = Event(uid=f"event/{NS}/payment-api-crashloop", name="payment-api-crashloop", namespace=NS)
    event.event_type = "Warning"
    event.reason = "BackOff"
    event.message = "Back-off restarting failed container payment in pod payment-api-0"
    event.involved_name = "payment-api-0"
    event.involved_kind = "Pod"
    graph.add_entity(event)

    event2 = Event(uid=f"event/{NS}/payment-api-db-refused", name="payment-api-db-refused", namespace=NS)
    event2.event_type = "Warning"
    event2.reason = "Failed"
    event2.message = "dial tcp 10.96.5.22:5432: connect: connection refused — db-primary has 0 ready endpoints"
    event2.involved_name = "payment-api-0"
    event2.involved_kind = "Pod"
    graph.add_entity(event2)


# ── Incident 2: analytics-worker — OOMKilled, memory limit drift ──────────────

def _add_analytics_worker(graph: OntologyGraph) -> None:
    # Anchor on Deployment (source of truth), not the ephemeral Pod
    deploy = Deployment(uid=f"deploy/{NS}/analytics-worker", name="analytics-worker", namespace=NS)
    deploy.replicas = 1
    deploy.ready_replicas = 0
    deploy.annotations = {
        "anchor.spec.template.spec.containers[0].resources.limits.memory":
            "256Mi [manifest] vs 50Mi [observed]",
        "drift.resources": "Helm declared memory limit 256Mi — deployed manifest enforces 50Mi",
    }
    graph.add_entity(deploy)

    pod = Pod(uid=f"pod/{NS}/analytics-worker", name="analytics-worker", namespace=NS)
    pod.phase = "OOMKilled"
    pod.restart_count = 12
    pod.owner_ref_kind = "Deployment"
    pod.owner_ref_name = "analytics-worker"
    pod.annotations = {
        "signal.oom": "Container worker OOM killed — limit 50Mi deployed, allocated 200Mi before kill",
    }
    graph.add_entity(pod)

    event = Event(uid=f"event/{NS}/analytics-oom", name="analytics-oom", namespace=NS)
    event.event_type = "Warning"
    event.reason = "OOMKilling"
    event.message = "Memory cgroup out of memory: container worker allocated 200Mi, enforced limit 50Mi"
    event.involved_name = "analytics-worker"
    event.involved_kind = "Pod"
    graph.add_entity(event)


# ── Incident 3: notification-svc — ImagePullBackOff, tag drift ───────────────

def _add_notification_svc(graph: OntologyGraph) -> None:
    deploy = Deployment(uid=f"deploy/{NS}/notification-svc", name="notification-svc", namespace=NS)
    deploy.replicas = 2
    deploy.ready_replicas = 0
    deploy.annotations = {
        "anchor.spec.template.spec.containers[0].image":
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

    event = Event(uid=f"event/{NS}/notification-imagepull", name="notification-imagepull", namespace=NS)
    event.event_type = "Warning"
    event.reason = "Failed"
    event.message = (
        "Failed to pull image myregistry.io/notification:latest: "
        "rpc error: code = NotFound: image not found"
    )
    event.involved_name = "notification-svc-0"
    event.involved_kind = "Pod"
    graph.add_entity(event)


# ── Incident 4: ml-inference — Pending, resource quota exceeded ───────────────

def _add_ml_inference(graph: OntologyGraph) -> None:
    deploy = Deployment(uid=f"deploy/{NS}/ml-inference", name="ml-inference", namespace=NS)
    deploy.replicas = 1
    deploy.ready_replicas = 0
    graph.add_entity(deploy)

    pod = Pod(uid=f"pod/{NS}/ml-inference-0", name="ml-inference-0", namespace=NS)
    pod.phase = "Pending"
    pod.restart_count = 0
    pod.owner_ref_kind = "Deployment"
    pod.owner_ref_name = "ml-inference"
    pod.annotations = {
        "signal.unschedulable": "0/1 nodes are available: Insufficient nvidia.com/gpu",
    }
    graph.add_entity(pod)

    event = Event(uid=f"event/{NS}/ml-inference-pending", name="ml-inference-pending", namespace=NS)
    event.event_type = "Warning"
    event.reason = "FailedScheduling"
    event.message = "0/1 nodes are available: 1 Insufficient nvidia.com/gpu"
    event.involved_name = "ml-inference-0"
    event.involved_kind = "Pod"
    graph.add_entity(event)


# ── Baseline: api-gateway — healthy ──────────────────────────────────────────

def _add_api_gateway(graph: OntologyGraph) -> None:
    deploy = Deployment(uid=f"deploy/{NS}/api-gateway", name="api-gateway", namespace=NS)
    deploy.replicas = 2
    deploy.ready_replicas = 2
    graph.add_entity(deploy)

    for i in range(2):
        pod = Pod(uid=f"pod/{NS}/api-gateway-{i}", name=f"api-gateway-{i}", namespace=NS)
        pod.phase = "Running"
        pod.restart_count = 0
        pod.owner_ref_kind = "Deployment"
        pod.owner_ref_name = "api-gateway"
        graph.add_entity(pod)

    svc = Service(uid=f"svc/{NS}/api-gateway", name="api-gateway", namespace=NS)
    graph.add_entity(svc)


# ── Cluster summary ───────────────────────────────────────────────────────────

INCIDENTS = [
    {
        "service":  "payment-api",
        "status":   "CrashLoopBackOff",
        "restarts": 47,
        "cause":    "db-primary scaled to 0 (Helm drift) — connection refused",
        "severity": "critical",
    },
    {
        "service":  "analytics-worker",
        "status":   "OOMKilled",
        "restarts": 12,
        "cause":    "Deployed limit 50Mi — chart fixed to 256Mi (undeployed drift)",
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
        "cause":    "GPU scheduling delay — node temporarily at capacity",
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

def generate_patch_diffs(graph: OntologyGraph) -> list[dict]:
    """
    Compute git-diff-style patches from anchor annotations on the graph.

    Each ``anchor.<field>`` annotation of the form
    ``"<manifest_val> [manifest] vs <observed_val> [observed]"``
    becomes one patch entry.  Nothing is hardcoded — all patches derive
    from the fake-cluster state injected by the scenario builder.
    """
    import re
    patches: list[dict] = []
    _pattern = re.compile(
        r"(.+?)\s*\[manifest\]\s*vs\s*(.+?)\s*\[observed",
        re.IGNORECASE,
    )
    for entity in graph.entities():
        ann = getattr(entity, "annotations", {}) or {}
        kind = getattr(entity.kind, "value", str(entity.kind)).lower()
        ns   = entity.namespace or ""
        name = entity.name
        for key, val_raw in ann.items():
            if not key.startswith("anchor."):
                continue
            m = _pattern.match(str(val_raw))
            if not m:
                continue
            manifest_val = m.group(1).strip()
            observed_val  = m.group(2).strip()
            field = key[len("anchor."):]
            # Last segment of the dotted field path is the YAML key
            yaml_key = field.split(".")[-1]
            diff = (
                f"--- a/{kind}/{ns}/{name}\n"
                f"+++ b/{kind}/{ns}/{name}\n"
                f"@@ field: {field} @@\n"
                f"-  {yaml_key}: {observed_val}\n"
                f"+  {yaml_key}: {manifest_val}\n"
            )
            patches.append({
                "entity":        f"{kind}/{ns}/{name}",
                "field":         field,
                "manifest_value": manifest_val,
                "observed_value": observed_val,
                "diff":          diff,
            })
    return patches


HEALED_INCIDENTS = [
    {"service": "payment-api",      "status": "Running", "restarts": 0, "cause": "—", "severity": "ok"},
    {"service": "analytics-worker", "status": "Running", "restarts": 0, "cause": "—", "severity": "ok"},
    {"service": "notification-svc", "status": "Running", "restarts": 0, "cause": "—", "severity": "ok"},
    {"service": "ml-inference",     "status": "Running",  "restarts": 0,
     "cause": "Scheduling delay resolved — GPU capacity freed",  "severity": "ok"},
    {"service": "api-gateway",      "status": "Running", "restarts": 0, "cause": "—", "severity": "ok"},
]
