"""
Canonical verdict envelope — the stable, IDP-facing projection of a completed
investigation.

This is deliberately a **projection over the existing models**, not a new source
of truth. It reuses the workflow state produced by
``services.investigation_service.run_investigation`` (the same state behind
``verdict_summary``) and the already-computed policy verdict / blast radius /
gate score. Nothing here re-derives a decision — it only reshapes one.

Honesty notes (see roadmap / decision-engine audit):
  * ``confidence_score`` is the deterministic diagnosis-label → gate-score map
    (HIGH=0.85, MEDIUM=0.65, LOW=0.62, missing=0.20), surfaced as computed by
    the DecisionEngine — it is not an independent calibrated probability.
  * ``blast_radius`` is a heuristic over remediation command strings, not a
    rendered-vs-live diff.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    source: str  # event | alert | trace | policy | anchor
    detail: str


class RemediationAction(BaseModel):
    commands: list[str] = Field(default_factory=list)


class RollbackAction(BaseModel):
    available: bool = False
    commands: list[str] = Field(default_factory=list)


def _evidence_from_report(report: dict[str, Any]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for src, key in (
        ("event", "events"),
        ("alert", "alerts"),
        ("trace", "traces"),
        ("policy", "policy_violations"),
        ("anchor", "anchor_fixes"),
    ):
        for detail in report.get(key) or []:
            items.append(EvidenceItem(source=src, detail=str(detail)))
    return items


def _gate_score(state: dict[str, Any]) -> float | None:
    """Reuse the score the policy gate actually used (recorded in edge_log),
    falling back to None rather than fabricating a value."""
    for entry in reversed(state.get("edge_log") or []):
        if entry.get("router") == "policy":
            score = (entry.get("snapshot") or {}).get("score")
            return float(score) if score is not None else None
    return None


def _next_steps(policy: str | None, reasons: list[str]) -> list[str]:
    if policy == "AUTO":
        return ["Remediation is eligible for automated apply — verify in target context first."]
    if policy == "NO_GO":
        return ["Do not apply automatically."] + list(reasons)
    # HUMAN_REVIEW (default / safest)
    return ["Operator review required before applying the proposed remediation."] + list(reasons)


class VerdictEnvelope(BaseModel):
    """Stable contract returned to IDP / portal / agent consumers."""

    session_id: str
    service: str | None = None
    namespace: str | None = None
    environment: str | None = None

    root_cause: str = ""
    confidence_label: str = ""           # HIGH | MEDIUM | LOW | "" (no analysis)
    confidence_score: float | None = None
    policy: Literal["AUTO", "HUMAN_REVIEW", "NO_GO"] | None = None
    blast_radius: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None

    evidence: list[EvidenceItem] = Field(default_factory=list)
    remediation: RemediationAction | None = None
    rollback: RollbackAction | None = None
    next_steps: list[str] = Field(default_factory=list)

    @classmethod
    def from_state(
        cls,
        session_id: str,
        state: dict[str, Any],
        *,
        service: str | None = None,
        namespace: str | None = None,
        environment: str | None = None,
    ) -> "VerdictEnvelope":
        report = state.get("report_dict") or {}
        br = state.get("blast_radius") or {}
        reasons = list(state.get("verdict_reasons") or [])
        policy = state.get("verdict") or None

        remediation_cmds = list(report.get("remediation") or [])
        rollback_cmds = list(report.get("rollback") or [])

        return cls(
            session_id=session_id,
            service=service,
            namespace=namespace,
            environment=environment,
            root_cause=report.get("root_cause") or "",
            confidence_label=state.get("confidence") or report.get("confidence") or "",
            confidence_score=_gate_score(state),
            policy=policy if policy in ("AUTO", "HUMAN_REVIEW", "NO_GO") else None,
            blast_radius=br.get("risk") if br.get("risk") in ("LOW", "MEDIUM", "HIGH", "CRITICAL") else None,
            evidence=_evidence_from_report(report),
            remediation=RemediationAction(commands=remediation_cmds) if remediation_cmds else None,
            rollback=RollbackAction(
                available=bool(br.get("rollback_available", bool(rollback_cmds))),
                commands=rollback_cmds,
            ) if rollback_cmds or br else None,
            next_steps=_next_steps(policy, reasons),
        )
