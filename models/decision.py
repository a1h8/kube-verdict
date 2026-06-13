"""Formal decision model — a typed wrapper over ``decision.policy_gate.evaluate``.

Carries both the verdict (AUTO / HUMAN_REVIEW / NO_GO) and the inputs it was
derived from, so the decision is serialisable and auditable inside the canonical
:class:`IncidentReport` instead of being recomputed by each consumer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from decision.policy_gate import evaluate as _gate_evaluate

Verdict = str  # "AUTO" | "HUMAN_REVIEW" | "NO_GO"


@dataclass
class Decision:
    verdict: Verdict = "HUMAN_REVIEW"
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0
    risk: str = "LOW"
    rollback_available: bool = True
    namespace: str = ""
    mc_win_rate: float = 1.0

    @classmethod
    def evaluate(
        cls,
        *,
        score: float,
        risk: str,
        rollback_available: bool,
        namespace: str = "",
        mc_win_rate: float = 1.0,
        max_switches_reached: bool = False,
    ) -> "Decision":
        gate = _gate_evaluate(
            score=score,
            risk=risk,
            rollback_available=rollback_available,
            namespace=namespace,
            mc_win_rate=mc_win_rate,
            max_switches_reached=max_switches_reached,
        )
        return cls(
            verdict=gate.verdict.value,
            reasons=list(gate.reasons),
            score=float(score),
            risk=risk,
            rollback_available=bool(rollback_available),
            namespace=namespace,
            mc_win_rate=float(mc_win_rate),
        )

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reasons": self.reasons,
            "score": round(self.score, 4),
            "risk": self.risk,
            "rollback_available": self.rollback_available,
            "namespace": self.namespace,
            "mc_win_rate": round(self.mc_win_rate, 4),
        }
