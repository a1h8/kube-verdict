"""Golden scenario inputs — one representative decision case per h-series fixture.

Each entry is the *deterministic* decision input (pre-LLM score + the proposed
remediation / rollback / namespace) that the DecisionEngine turns into a verdict.
Kept here as data so the golden guard replays them and diffs the verdict +
blast-radius against a recorded baseline (``baseline.json``), failing CI on drift.
"""
from __future__ import annotations

# id → decision input
SCENARIOS: dict[str, dict] = {
    "h001_crashloopbackoff": {
        "score": 0.62,
        "remediation": ["kubectl create configmap api-config -n production --from-file=app.conf"],
        "affected": ["Pod/api-1", "Pod/api-2"],
        "rollback": ["kubectl delete configmap api-config -n production"],
        "namespace": "production",
    },
    "h002_imagepullbackoff": {
        "score": 0.65,
        "remediation": ["kubectl set image deployment/ml ml=registry/ml:v2 -n staging"],
        "affected": ["Deployment/ml"],
        "rollback": ["kubectl rollout undo deployment/ml -n staging"],
        "namespace": "staging",
    },
    "h003_oomkilled": {
        "score": 0.85,
        "remediation": ["helm upgrade api ./chart -n staging --set resources.limits.memory=512Mi"],
        "affected": ["Deployment/api"],
        "rollback": ["helm rollback api -n staging"],
        "namespace": "staging",
    },
    "h004_missing_configmap": {
        "score": 0.62,
        "remediation": ["kubectl create configmap app-config -n staging"],
        "affected": ["Pod/app"],
        "rollback": ["kubectl delete configmap app-config -n staging"],
        "namespace": "staging",
    },
    "h005_rbac_forbidden": {
        "score": 0.65,
        "remediation": ["kubectl create clusterrolebinding api-admin --clusterrole=admin --serviceaccount=default:api"],
        "affected": ["ClusterRoleBinding/api-admin"],
        "rollback": ["kubectl delete clusterrolebinding api-admin"],
        "namespace": "",
    },
    "h006_networkpolicy_blocked": {
        "score": 0.47,
        "remediation": ["kubectl edit networkpolicy default-deny -n production"],
        "affected": ["NetworkPolicy/default-deny"],
        "rollback": [],
        "namespace": "production",
    },
    "h007_hpa_no_metrics": {
        "score": 0.62,
        "remediation": ["kubectl apply -f metrics-server.yaml"],
        "affected": ["Deployment/metrics-server"],
        "rollback": ["kubectl delete -f metrics-server.yaml"],
        "namespace": "kube-system",
    },
    "h008_init_container_fail": {
        "score": 0.65,
        "remediation": ["kubectl set image deployment/app migrate=registry/migrate:v2 -n staging"],
        "affected": ["Deployment/app"],
        "rollback": ["kubectl rollout undo deployment/app -n staging"],
        "namespace": "staging",
    },
    "h009_liveness_probe_loop": {
        "score": 0.70,
        "remediation": ["helm upgrade search ./chart -n production --set livenessProbe.timeoutSeconds=5"],
        "affected": ["Deployment/search"],
        "rollback": ["helm rollback search -n production"],
        "namespace": "production",
    },
    "h010_resource_quota_exceeded": {
        "score": 0.62,
        "remediation": ["kubectl patch resourcequota compute -n staging --patch '{...}'"],
        "affected": ["ResourceQuota/compute"],
        "rollback": ["kubectl patch resourcequota compute -n staging --patch '{...}'"],
        "namespace": "staging",
    },
}


def replay(case_id: str) -> dict:
    """Run one scenario through the deterministic decision layer → verdict + risk."""
    from models import BlastRadius, Decision

    s = SCENARIOS[case_id]
    br = BlastRadius.from_remediation(s["remediation"], s["affected"], s["rollback"])
    dec = Decision.evaluate(
        score=s["score"],
        risk=br.risk,
        rollback_available=br.rollback_available,
        namespace=s["namespace"],
    )
    return {"verdict": dec.verdict, "risk": br.risk, "rollback_available": br.rollback_available}


def replay_all() -> dict[str, dict]:
    return {cid: replay(cid) for cid in SCENARIOS}
