"""
DecisionEngine: pure, LLM-free orchestrator that turns a completed RCA into a
policy verdict (AUTO / HUMAN_REVIEW / NO_GO).

It owns the diagnosis-confidence → gate-score mapping and delegates the final
classification to :func:`decision.policy_gate.evaluate`. No LangGraph, no I/O —
fully unit-testable from an ``IncidentReport`` plus the blast-radius / Monte
Carlo inputs.
"""
from __future__ import annotations

from decision.models import DecisionResult, IncidentReport
from decision.policy_gate import evaluate as pg_evaluate

# LLM diagnosis confidence label → policy-gate score.
#   LOW / MEDIUM → HUMAN_REVIEW (operator decides)
#   HIGH         → eligible for AUTO when blast-radius + MC + namespace also pass
#   "" (missing) → 0.20 → NO_GO (workflow produced no analysis)
_LLM_SCORE: dict[str, float] = {
    "HIGH": 0.85, "MEDIUM": 0.65, "LOW": 0.62, "": 0.20,
}
_DEFAULT_SCORE: float = 0.65  # unknown label → treat as MEDIUM


class DecisionEngine:
    """Stateless policy decision engine."""

    def score_for(self, confidence: str) -> float:
        """Map an LLM diagnosis confidence label to a policy-gate score."""
        return _LLM_SCORE.get((confidence or "").upper(), _DEFAULT_SCORE)

    def decide(
        self,
        report: IncidentReport,
        *,
        risk: str = "HIGH",
        rollback_available: bool = False,
        namespace: str = "",
        mc_win_rate: float = 1.0,
        max_switches_reached: bool = False,
    ) -> DecisionResult:
        """Classify a remediation given the report and its safety context.

        Defaults are the conservative ones used when a stage produced no signal:
        ``risk=HIGH`` (blast radius unknown) and ``rollback_available=False``
        both push away from AUTO.
        """
        score = self.score_for(report.confidence)
        gate = pg_evaluate(
            score=score,
            risk=risk,
            rollback_available=rollback_available,
            namespace=namespace,
            mc_win_rate=mc_win_rate,
            max_switches_reached=max_switches_reached,
        )
        return DecisionResult(
            verdict=gate.verdict,
            reasons=gate.reasons,
            score=score,
            risk=risk,
            rollback_available=rollback_available,
            namespace=namespace,
            mc_win_rate=mc_win_rate,
            max_switches_reached=max_switches_reached,
        )