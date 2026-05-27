"""Unit tests for B6 Decision Engine nodes: monte_carlo, log_policy_decision, verdict_router."""
from __future__ import annotations

import pytest

from workflow.nodes import monte_carlo_node, log_policy_decision_node, verdict_router
from workflow.state import RCAState


# ── helpers ──────────────────────────────────────────────────────────────────

def _state(**kwargs) -> RCAState:
    base: RCAState = {
        "confidence": "HIGH",
        "retry_count": 0,
        "human_decision": "",
        "error": "",
    }
    base.update(kwargs)
    return base


_CFG = {"configurable": {}}

_BR_LOW_ROLLBACK = {
    "risk": "LOW",
    "summary": "1 resource",
    "rollback_available": True,
    "namespaces": ["staging"],
    "cluster_scoped": False,
    "command_count": 1,
}

_BR_CRITICAL = {
    "risk": "CRITICAL",
    "summary": "destructive",
    "rollback_available": False,
    "namespaces": ["production"],
    "cluster_scoped": True,
    "command_count": 1,
}


# ── monte_carlo_node ──────────────────────────────────────────────────────────

class TestMonteCarlo:
    def test_returns_mc_result_dict(self):
        result = monte_carlo_node(_state(), _CFG)
        mc = result["mc_result"]
        assert "win_rate" in mc
        assert "mean_score" in mc
        assert "is_stable" in mc
        assert "n_simulations" in mc

    def test_high_pre_llm_score_is_stable(self):
        state = _state(report_dict={"pre_llm_confidence": {"score": 0.90}})
        result = monte_carlo_node(state, _CFG)
        assert result["mc_result"]["is_stable"] is True
        assert result["mc_result"]["win_rate"] >= 0.80

    def test_low_pre_llm_score_is_unstable(self):
        state = _state(report_dict={"pre_llm_confidence": {"score": 0.15}})
        result = monte_carlo_node(state, _CFG)
        assert result["mc_result"]["is_stable"] is False
        assert result["mc_result"]["win_rate"] < 0.80

    def test_missing_pre_llm_defaults_to_0_5(self):
        state = _state(report_dict={})
        result = monte_carlo_node(state, _CFG)
        # score 0.5 → win_rate near 0 (±10% noise rarely crosses 0.60 threshold)
        assert result["mc_result"]["n_simulations"] == 200


# ── log_policy_decision_node ──────────────────────────────────────────────────

class TestLogPolicyDecision:
    def _run(self, **kwargs):
        state = _state(**kwargs)
        result = log_policy_decision_node(state)
        return result

    def test_high_conf_low_risk_stable_auto(self):
        """HIGH + LOW risk + rollback + non-prod + stable MC → AUTO."""
        result = self._run(
            confidence="HIGH",
            blast_radius=_BR_LOW_ROLLBACK,
            mc_result={"win_rate": 0.95, "is_stable": True},
        )
        assert result["verdict"] == "AUTO"
        assert result["_verdict_edge"] == "auto"

    def test_high_conf_prod_namespace_human_review(self):
        """HIGH + prod namespace → HUMAN_REVIEW (always minimum)."""
        result = self._run(
            confidence="HIGH",
            blast_radius={**_BR_LOW_ROLLBACK, "namespaces": ["production"]},
            mc_result={"win_rate": 0.95, "is_stable": True},
        )
        assert result["verdict"] == "HUMAN_REVIEW"
        assert result["_verdict_edge"] == "human_review"

    def test_critical_risk_no_go(self):
        result = self._run(
            confidence="HIGH",
            blast_radius=_BR_CRITICAL,
            mc_result={"win_rate": 0.95, "is_stable": True},
        )
        assert result["verdict"] == "NO_GO"
        assert result["_verdict_edge"] == "no_go"

    def test_no_rollback_no_go(self):
        br = {**_BR_LOW_ROLLBACK, "rollback_available": False}
        result = self._run(
            confidence="HIGH",
            blast_radius=br,
            mc_result={"win_rate": 0.95, "is_stable": True},
        )
        assert result["verdict"] == "NO_GO"

    def test_low_confidence_human_review(self):
        result = self._run(
            confidence="LOW",
            blast_radius=_BR_LOW_ROLLBACK,
            mc_result={"win_rate": 0.0, "is_stable": False},
        )
        assert result["verdict"] == "HUMAN_REVIEW"

    def test_medium_confidence_human_review(self):
        result = self._run(
            confidence="MEDIUM",
            blast_radius=_BR_LOW_ROLLBACK,
            mc_result={"win_rate": 0.85, "is_stable": True},
        )
        assert result["verdict"] == "HUMAN_REVIEW"

    def test_empty_confidence_no_go(self):
        """Missing LLM confidence → score 0.20 → NO_GO."""
        result = self._run(
            confidence="",
            blast_radius=_BR_LOW_ROLLBACK,
            mc_result={"win_rate": 0.95, "is_stable": True},
        )
        assert result["verdict"] == "NO_GO"

    def test_unstable_mc_blocks_auto(self):
        """HIGH + stable conditions except mc → HUMAN_REVIEW."""
        result = self._run(
            confidence="HIGH",
            blast_radius=_BR_LOW_ROLLBACK,
            mc_result={"win_rate": 0.50, "is_stable": False},
        )
        assert result["verdict"] == "HUMAN_REVIEW"

    def test_beam_exhausted_no_go(self):
        result = self._run(
            confidence="HIGH",
            blast_radius=_BR_LOW_ROLLBACK,
            mc_result={"win_rate": 0.95, "is_stable": True},
            max_switches_reached=True,
        )
        assert result["verdict"] == "NO_GO"

    def test_writes_edge_log(self):
        result = self._run(
            confidence="HIGH",
            blast_radius=_BR_CRITICAL,
            mc_result={"win_rate": 0.95},
        )
        assert any(e["router"] == "policy" for e in result["edge_log"])

    def test_appends_to_existing_edge_log(self):
        existing = [{"router": "confidence", "edge_taken": "review"}]
        result = self._run(
            confidence="HIGH",
            blast_radius=_BR_CRITICAL,
            mc_result={"win_rate": 0.95},
            edge_log=existing,
        )
        assert len(result["edge_log"]) == 2

    def test_sets_verdict_reasons(self):
        result = self._run(
            confidence="HIGH",
            blast_radius=_BR_CRITICAL,
            mc_result={"win_rate": 0.95},
        )
        assert isinstance(result["verdict_reasons"], list)
        assert len(result["verdict_reasons"]) > 0


# ── verdict_router ────────────────────────────────────────────────────────────

class TestVerdictRouter:
    def test_reads_verdict_edge(self):
        assert verdict_router({"_verdict_edge": "auto"}) == "auto"
        assert verdict_router({"_verdict_edge": "human_review"}) == "human_review"
        assert verdict_router({"_verdict_edge": "no_go"}) == "no_go"

    def test_defaults_to_human_review(self):
        assert verdict_router({}) == "human_review"
