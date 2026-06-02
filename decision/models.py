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


@dataclass
class BlastRadius:
    """Typed view of a blast-radius assessment.

    Mirrors the dict produced by ``remediation.blast_radius.compute_blast_radius``.
    Note: this is a **heuristic** over the remediation command strings (verb /
    namespace / kind / cluster-scope / affected-count), not a rendered-vs-live
    diff — a triage signal, not a guarantee of impact.
    """

    risk: str = "LOW"  # LOW | MEDIUM | HIGH | CRITICAL
    summary: str = ""
    resources: list[str] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)
    cluster_scoped: bool = False
    command_count: int = 0
    rollback_available: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "BlastRadius":
        d = d or {}
        return cls(
            risk=d.get("risk", "LOW"),
            summary=d.get("summary", ""),
            resources=list(d.get("resources") or []),
            namespaces=list(d.get("namespaces") or []),
            cluster_scoped=bool(d.get("cluster_scoped", False)),
            command_count=int(d.get("command_count", 0)),
            rollback_available=bool(d.get("rollback_available", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "summary": self.summary,
            "resources": list(self.resources),
            "namespaces": list(self.namespaces),
            "cluster_scoped": self.cluster_scoped,
            "command_count": self.command_count,
            "rollback_available": self.rollback_available,
        }


def _rollback_strategy(commands: list[str]) -> str:
    """Classify a rollback command list into a strategy label."""
    joined = " ".join(commands)
    if "helm rollback" in joined:
        return "helm_rollback"
    if "rollout undo" in joined:
        return "rollout_undo"
    if "delete -f" in joined:
        return "apply_previous"
    if "delete" in joined:
        return "delete"
    return "command"


@dataclass
class RollbackPlan:
    """Typed view of the recovery path derived for a remediation.

    ``available=False`` is the hard NO_GO trigger in the policy gate — without a
    recovery path the decision cannot be AUTO or HUMAN_REVIEW.
    """

    available: bool = False
    strategy: str = "none"  # helm_rollback | rollout_undo | apply_previous | delete | command | none
    commands: list[str] = field(default_factory=list)

    @classmethod
    def from_commands(cls, commands: list[str] | None) -> "RollbackPlan":
        commands = list(commands or [])
        if not commands:
            return cls(available=False, strategy="none", commands=[])
        return cls(available=True, strategy=_rollback_strategy(commands), commands=commands)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "strategy": self.strategy,
            "commands": list(self.commands),
        }
