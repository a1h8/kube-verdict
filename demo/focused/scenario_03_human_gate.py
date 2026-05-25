"""
Scenario 03 — CrashLoopBackOff + human gate (risky DB change)
Single incident: payment-api down because db-primary scaled to 0
The workflow detects the fix (scale db-primary to 1) but flags it as RISKY
→ human review required before applying (prod database, high impact)
"""
from __future__ import annotations
from ontology.entities import Deployment, K8sEvent as Event, Namespace, Pod, Service
from ontology.graph import OntologyGraph

NS = "kubeverdict-demo"

TITLE  = "CrashLoopBackOff — human gate (risky DB change)"
QUERY  = (
    "payment-api is in CrashLoopBackOff — DB connection refused. "
    "db-primary has 0 ready endpoints. Identify the root cause and remediation. "
    "This is a production database — flag if human approval is required."
)

INCIDENTS = [
    {
        "service":  "payment-api",
        "status":   "CrashLoopBackOff",
        "restarts": 47,
        "cause":    "db-primary scaled to 0 — connection refused (requires human approval)",
        "severity": "critical",
    },
]

HEALED_INCIDENTS = [
    {"service": "payment-api", "status": "Running", "restarts": 0, "cause": "—", "severity": "ok"},
]

# This scenario MUST NOT auto-approve — human review is the point of the demo
AUTO_APPROVE = False


def build_graph() -> OntologyGraph:
    graph = OntologyGraph(server_version="v1.31.4")
    graph.add_entity(Namespace(uid=f"ns/{NS}", name=NS, namespace=NS))

    # Root cause: db-primary scaled to 0 (Helm drift)
    db = Deployment(uid=f"deploy/{NS}/db-primary", name="db-primary", namespace=NS)
    db.replicas = 1
    db.ready_replicas = 0
    db.annotations = {
        "anchor.spec.replicas": "1 [manifest] vs 0 [observed]",
        "drift.replicas":       "Helm declared replicas=1 — cluster running replicas=0",
        "risk.level":           "HIGH — production database, scaling affects all dependent services",
    }
    graph.add_entity(db)

    svc = Service(uid=f"svc/{NS}/db-primary", name="db-primary", namespace=NS)
    svc.annotations = {"missing.endpoints": "Service db-primary has 0 ready endpoints"}
    graph.add_entity(svc)

    # Cascade: payment-api crashing
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

    ev = Event(uid=f"event/{NS}/payment-db-refused", name="payment-db-refused", namespace=NS)
    ev.event_type = "Warning"
    ev.reason = "Failed"
    ev.message = "dial tcp 10.96.5.22:5432: connect: connection refused — db-primary has 0 ready endpoints"
    ev.involved_name = "payment-api-0"
    ev.involved_kind = "Pod"
    graph.add_entity(ev)

    return graph
