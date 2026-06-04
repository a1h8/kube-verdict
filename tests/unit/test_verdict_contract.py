"""Unit tests for the VerdictEnvelope projection (api/verdict_contract.py)."""
from api.verdict_contract import VerdictEnvelope


_STATE = {
    "confidence": "HIGH",
    "verdict": "HUMAN_REVIEW",
    "verdict_reasons": ["namespace 'prod' is production — always HUMAN_REVIEW minimum"],
    "blast_radius": {"risk": "MEDIUM", "rollback_available": True, "namespaces": ["prod"]},
    "report_dict": {
        "root_cause": "No PersistentVolume matches storage class 'standard' 10Gi.",
        "confidence": "HIGH",
        "remediation": ["kubectl apply -f pv-standard-10gi.yaml"],
        "rollback": ["kubectl delete -f pv-standard-10gi.yaml"],
        "events": ["Warning FailedMount pod/api-xyz"],
        "alerts": ["FIRING KubePodCrashLooping severity=critical"],
        "policy_violations": ["FAIL require-limits: container api has no memory limit"],
    },
    "edge_log": [
        {"router": "confidence", "edge_taken": "review", "snapshot": {"score": 0.0}},
        {"router": "policy", "edge_taken": "human_review", "snapshot": {"score": 0.85}},
    ],
}


def test_from_state_projects_core_fields():
    env = VerdictEnvelope.from_state(
        "sess-1", _STATE, service="payment-api", namespace="prod", environment="prod"
    )
    assert env.session_id == "sess-1"
    assert env.service == "payment-api"
    assert env.namespace == "prod"
    assert env.environment == "prod"
    assert env.root_cause.startswith("No PersistentVolume")
    assert env.confidence_label == "HIGH"
    assert env.policy == "HUMAN_REVIEW"
    assert env.blast_radius == "MEDIUM"


def test_confidence_score_reuses_gate_score_not_fabricated():
    # 0.85 comes from the policy edge_log snapshot, not a recomputed/invented value.
    env = VerdictEnvelope.from_state("s", _STATE)
    assert env.confidence_score == 0.85


def test_remediation_and_rollback_projected():
    env = VerdictEnvelope.from_state("s", _STATE)
    assert env.remediation is not None
    assert env.remediation.commands == ["kubectl apply -f pv-standard-10gi.yaml"]
    assert env.rollback is not None
    assert env.rollback.available is True
    assert env.rollback.commands == ["kubectl delete -f pv-standard-10gi.yaml"]


def test_evidence_sources_tagged():
    env = VerdictEnvelope.from_state("s", _STATE)
    sources = {e.source for e in env.evidence}
    assert {"event", "alert", "policy"} <= sources


def test_next_steps_reflect_policy():
    env = VerdictEnvelope.from_state("s", _STATE)
    assert any("review" in step.lower() for step in env.next_steps)


def test_empty_state_is_safe():
    env = VerdictEnvelope.from_state("s", {})
    assert env.policy is None
    assert env.blast_radius is None
    assert env.confidence_score is None
    assert env.confidence_label == ""
    assert env.remediation is None


def test_unknown_enum_values_drop_to_none():
    env = VerdictEnvelope.from_state(
        "s", {"verdict": "WAT", "blast_radius": {"risk": "NUCLEAR"}}
    )
    assert env.policy is None
    assert env.blast_radius is None
