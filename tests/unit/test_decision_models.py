"""Schema/contract tests for the canonical decision-layer models.

`test_incident_report_schema` is the contract guard: it pins the canonical
IncidentReport field set so any change to the schema is deliberate (it will be
extended when the model grows into the full envelope). The BlastRadius /
RollbackPlan tests cover the new formal types + their dict round-trips, so every
piece that will compose into the envelope serializes to stable JSON.
"""
from __future__ import annotations

import json
from dataclasses import fields

from decision.models import BlastRadius, IncidentReport, RollbackPlan


# ── IncidentReport schema contract ──────────────────────────────────────────────

def test_incident_report_schema():
    """Pin the canonical IncidentReport field set (deliberate-change guard)."""
    names = {f.name for f in fields(IncidentReport)}
    assert names == {
        "query",
        "summary",
        "root_cause",
        "confidence",
        "causal_chain",
        "affected",
        "remediation",
        "rollback",
    }


def test_incident_report_round_trips_report_dict():
    """A report_dict projected onto IncidentReport keeps the core fields stable."""
    src = {
        "query": "pods crashlooping",
        "summary": "api OOMKilled",
        "root_cause": "memory limit too low",
        "confidence": "HIGH",
        "causal_chain": ["limit", "OOM", "restart"],
        "affected": ["Deployment/payment/api"],
        "remediation": ["kubectl set resources ..."],
        "rollback": ["kubectl rollout undo ..."],
        # extra telemetry keys are ignored, not an error
        "raw_analysis": "…",
        "context_stats": {"seeds": 3},
    }
    report = IncidentReport.from_report_dict(src)
    for key in (
        "query", "summary", "root_cause", "confidence",
        "causal_chain", "affected", "remediation", "rollback",
    ):
        assert getattr(report, key) == src[key]


# ── BlastRadius ─────────────────────────────────────────────────────────────────

class TestBlastRadius:
    def test_schema(self):
        names = {f.name for f in fields(BlastRadius)}
        assert names == {
            "risk", "summary", "resources", "namespaces",
            "cluster_scoped", "command_count", "rollback_available",
        }

    def test_from_dict_to_dict_round_trip(self):
        d = {
            "risk": "HIGH",
            "summary": "2 resource(s) — cluster-scoped",
            "resources": ["ClusterRole/admin"],
            "namespaces": ["kube-system"],
            "cluster_scoped": True,
            "command_count": 2,
            "rollback_available": True,
        }
        br = BlastRadius.from_dict(d)
        assert br.risk == "HIGH"
        assert br.cluster_scoped is True
        assert br.to_dict() == d
        # JSON-serializable (stable contract)
        assert json.loads(json.dumps(br.to_dict())) == d

    def test_from_dict_defaults(self):
        br = BlastRadius.from_dict(None)
        assert br.risk == "LOW"
        assert br.resources == []
        assert br.rollback_available is True


# ── RollbackPlan ────────────────────────────────────────────────────────────────

class TestRollbackPlan:
    def test_schema(self):
        names = {f.name for f in fields(RollbackPlan)}
        assert names == {"available", "strategy", "commands"}

    def test_empty_is_unavailable(self):
        plan = RollbackPlan.from_commands([])
        assert plan.available is False
        assert plan.strategy == "none"
        assert plan.commands == []

    def test_helm_rollback_strategy(self):
        plan = RollbackPlan.from_commands(["helm rollback api -n prod"])
        assert plan.available is True
        assert plan.strategy == "helm_rollback"

    def test_rollout_undo_strategy(self):
        plan = RollbackPlan.from_commands(["kubectl rollout undo deployment/api -n prod"])
        assert plan.strategy == "rollout_undo"

    def test_apply_previous_strategy(self):
        plan = RollbackPlan.from_commands(["kubectl delete -f manifest.yaml"])
        assert plan.strategy == "apply_previous"

    def test_delete_strategy(self):
        plan = RollbackPlan.from_commands(["kubectl delete clusterrolebinding api"])
        assert plan.strategy == "delete"

    def test_to_dict_json_serializable(self):
        plan = RollbackPlan.from_commands(["helm rollback api -n prod"])
        d = plan.to_dict()
        assert json.loads(json.dumps(d)) == d
