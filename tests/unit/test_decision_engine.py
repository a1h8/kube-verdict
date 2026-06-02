"""Unit tests for the pure DecisionEngine + IncidentReport (PR1, Track 1)."""
from __future__ import annotations

import pytest

from decision.decision_engine import DecisionEngine
from decision.models import DecisionResult, IncidentReport
from decision.policy_gate import Verdict


@pytest.fixture
def engine() -> DecisionEngine:
    return DecisionEngine()


# ── IncidentReport.from_report_dict ────────────────────────────────────────────

class TestIncidentReportProjection:
    def test_full_dict(self):
        report = IncidentReport.from_report_dict({
            "query": "pods crashlooping",
            "summary": "api OOMKilled",
            "root_cause": "memory limit too low",
            "confidence": "HIGH",
            "causal_chain": ["limit低", "OOM", "restart"],
            "affected": ["Deployment/payment/api"],
            "remediation": ["kubectl set resources ..."],
            "rollback": ["kubectl rollout undo ..."],
        })
        assert report.confidence == "HIGH"
        assert report.root_cause == "memory limit too low"
        assert report.remediation == ["kubectl set resources ..."]
        assert report.affected == ["Deployment/payment/api"]

    def test_none_yields_empty(self):
        report = IncidentReport.from_report_dict(None)
        assert report.confidence == ""
        assert report.remediation == []
        assert report.causal_chain == []

    def test_empty_dict_yields_empty(self):
        report = IncidentReport.from_report_dict({})
        assert report == IncidentReport()

    def test_none_valued_keys_coerced(self):
        report = IncidentReport.from_report_dict({
            "confidence": None, "remediation": None, "root_cause": None,
        })
        assert report.confidence == ""
        assert report.remediation == []
        assert report.root_cause == ""

    def test_lists_are_copies(self):
        src = {"remediation": ["a"]}
        report = IncidentReport.from_report_dict(src)
        report.remediation.append("b")
        assert src["remediation"] == ["a"]  # original untouched


# ── score_for: confidence label → gate score ───────────────────────────────────

class TestScoreFor:
    @pytest.mark.parametrize("label,expected", [
        ("HIGH", 0.85), ("MEDIUM", 0.65), ("LOW", 0.62), ("", 0.20),
    ])
    def test_known_labels(self, engine, label, expected):
        assert engine.score_for(label) == expected

    def test_case_insensitive(self, engine):
        assert engine.score_for("high") == 0.85
        assert engine.score_for("Low") == 0.62

    def test_none_is_empty(self, engine):
        assert engine.score_for(None) == 0.20

    def test_unknown_label_defaults_to_medium(self, engine):
        assert engine.score_for("GARBAGE") == 0.65


# ── decide: verdict classification ──────────────────────────────────────────────

class TestDecide:
    def _high(self) -> IncidentReport:
        return IncidentReport(confidence="HIGH", remediation=["fix"])

    def test_auto(self, engine):
        result = engine.decide(
            self._high(), risk="LOW", rollback_available=True,
            namespace="staging", mc_win_rate=0.95,
        )
        assert result.verdict is Verdict.AUTO
        assert result.edge == "auto"
        assert result.score == 0.85

    def test_prod_namespace_blocks_auto(self, engine):
        result = engine.decide(
            self._high(), risk="LOW", rollback_available=True,
            namespace="production", mc_win_rate=0.95,
        )
        assert result.verdict is Verdict.HUMAN_REVIEW
        assert result.edge == "human_review"

    def test_unstable_mc_blocks_auto(self, engine):
        result = engine.decide(
            self._high(), risk="LOW", rollback_available=True,
            namespace="staging", mc_win_rate=0.50,
        )
        assert result.verdict is Verdict.HUMAN_REVIEW

    def test_medium_is_human_review(self, engine):
        result = engine.decide(
            IncidentReport(confidence="MEDIUM"), risk="LOW",
            rollback_available=True, namespace="staging", mc_win_rate=0.95,
        )
        assert result.verdict is Verdict.HUMAN_REVIEW

    def test_critical_risk_no_go(self, engine):
        result = engine.decide(
            self._high(), risk="CRITICAL", rollback_available=True,
            namespace="staging", mc_win_rate=0.95,
        )
        assert result.verdict is Verdict.NO_GO
        assert result.edge == "no_go"

    def test_no_rollback_no_go(self, engine):
        result = engine.decide(
            self._high(), risk="LOW", rollback_available=False,
            namespace="staging", mc_win_rate=0.95,
        )
        assert result.verdict is Verdict.NO_GO

    def test_empty_confidence_no_go(self, engine):
        result = engine.decide(
            IncidentReport(confidence=""), risk="LOW",
            rollback_available=True, namespace="staging", mc_win_rate=0.95,
        )
        assert result.verdict is Verdict.NO_GO

    def test_max_switches_no_go(self, engine):
        result = engine.decide(
            self._high(), risk="LOW", rollback_available=True,
            namespace="staging", mc_win_rate=0.95, max_switches_reached=True,
        )
        assert result.verdict is Verdict.NO_GO

    def test_conservative_defaults_are_not_auto(self, engine):
        # No safety context provided: risk defaults HIGH, rollback False → NO_GO.
        result = engine.decide(self._high())
        assert result.verdict is Verdict.NO_GO

    def test_result_carries_inputs(self, engine):
        result = engine.decide(
            self._high(), risk="LOW", rollback_available=True,
            namespace="staging", mc_win_rate=0.95,
        )
        assert isinstance(result, DecisionResult)
        assert result.risk == "LOW"
        assert result.rollback_available is True
        assert result.namespace == "staging"
        assert result.mc_win_rate == 0.95
        assert result.reasons  # non-empty
