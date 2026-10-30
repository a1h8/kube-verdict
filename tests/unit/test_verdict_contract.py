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


def test_pull_request_absent_by_default():
    env = VerdictEnvelope.from_state("s", _STATE)
    assert env.pull_request is None


def test_pull_request_projected_when_change_opened():
    state = {
        **_STATE,
        "proposed_change": {
            "url": "https://github.com/acme/api/pull/7",
            "branch": "kubeverdict/fix-api-abcd1234",
            "provider": "github",
            "draft": True,
            "number": 7,
        },
    }
    env = VerdictEnvelope.from_state("s", state)
    assert env.pull_request is not None
    assert env.pull_request.url.endswith("/pull/7")
    assert env.pull_request.provider == "github"
    assert env.pull_request.draft is True
    assert env.pull_request.number == 7


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


# ── docs/fix-plan-001.md, H72: the envelope says what the structured diagnosis concluded ──────

_DIAG = {"outcome": "cause", "confidence": "MEDIUM", "score": 0.65, "failed_collectors": [],
         "diagnoses": [{"family": "certificate", "cause": "The TLS certificate has expired.",
                        "location": "Issuer/production/letsencrypt-prod", "score": 0.65,
                        "label": "MEDIUM", "rule": "cert.expired", "checks": [], "signal_ids": []}]}


def test_the_ranked_diagnoses_and_the_disagreement_are_projected():
    state = {**_STATE, "diagnosis": _DIAG, "llm_disagreement": "the LLM named another cause"}
    env = VerdictEnvelope.from_state("s", state)
    assert env.outcome == "cause"
    assert [(d.family, d.location, d.rule) for d in env.diagnoses] == [
        ("certificate", "Issuer/production/letsencrypt-prod", "cert.expired")]
    assert env.llm_disagreement == "the LLM named another cause"


def test_the_diagnosis_is_read_from_the_report_too():
    report = {**_STATE["report_dict"], "diagnosis": _DIAG}
    assert VerdictEnvelope.from_state("s", {**_STATE, "report_dict": report}).outcome == "cause"


def test_an_abstention_speaks_of_no_remediation():
    for outcome, failed in (("no_incident", []), ("insufficient_evidence", ["loki"])):
        diag = {"outcome": outcome, "diagnoses": [], "failed_collectors": failed}
        env = VerdictEnvelope.from_state("s", {**_STATE, "diagnosis": diag})
        assert env.outcome == outcome and env.failed_collectors == failed
        assert not any("proposed remediation" in s for s in env.next_steps)
    assert "loki" in env.next_steps[0]
    # control: a found cause still asks for review of the proposed remediation
    env = VerdictEnvelope.from_state("s", {**_STATE, "diagnosis": _DIAG})
    assert any("proposed remediation" in s for s in env.next_steps)


def test_a_real_no_incident_run_projects_as_one():
    from tests.integration.workflow_harness import run_case
    run = run_case("h022")
    env = VerdictEnvelope.from_state("h022", run.state)
    assert env.outcome == "no_incident" and env.diagnoses == []
    assert env.next_steps[0].startswith("No incident diagnosed")
