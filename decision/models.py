"""
Canonical decision-layer data models.

`IncidentReport` is the typed projection of the workflow ``report_dict``
(``RCAReport.to_dict()``) that the DecisionEngine consumes — a stable, testable
contract independent of the LangGraph state shape.

`DecisionResult` is the full output of a policy decision: the verdict plus the
exact inputs it was derived from, so a decision is reproducible and auditable
without re-running the workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from decision.policy_gate import Verdict


@dataclass
class IncidentReport:
    """Typed view of a completed RCA report (subset of ``RCAReport.to_dict()``)."""

    query: str = ""
    summary: str = ""
    root_cause: str = ""
    confidence: str = ""  # LLM diagnosis label: HIGH | MEDIUM | LOW | ""
    causal_chain: list[str] = field(default_factory=list)
    affected: list[str] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    rollback: list[str] = field(default_factory=list)

    @classmethod
    def from_report_dict(cls, d: dict[str, Any] | None) -> "IncidentReport":
        """Project a workflow ``report_dict`` onto the typed model.

        Tolerant of missing keys and ``None`` values — an absent report yields
        an empty IncidentReport rather than raising.
        """
        d = d or {}
        return cls(
            query=d.get("query") or "",
            summary=d.get("summary") or "",
            root_cause=d.get("root_cause") or "",
            confidence=d.get("confidence") or "",
            causal_chain=list(d.get("causal_chain") or []),
            affected=list(d.get("affected") or []),
            remediation=list(d.get("remediation") or []),
            rollback=list(d.get("rollback") or []),
        )


@dataclass
class DecisionResult:
    """Outcome of a policy decision plus the inputs it was derived from."""

    verdict: Verdict
    reasons: list[str]
    score: float
    risk: str
    rollback_available: bool
    namespace: str
    mc_win_rate: float
    max_switches_reached: bool

    @property
    def edge(self) -> str:
        """Router edge name for ``verdict_router``: auto | human_review | no_go."""
        return self.verdict.value.lower()
