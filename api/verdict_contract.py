"""
Canonical verdict envelope — the stable, IDP-facing projection of a completed
investigation.

This is deliberately a **projection over the existing models**, not a new source
of truth. It reuses the workflow state produced by
``services.investigation_service.run_investigation`` (the same state behind
``verdict_summary``) and the already-computed policy verdict / blast radius /
gate score. Nothing here re-derives a decision — it only reshapes one.

The cause is the structured diagnosis (docs/structured-in-product.md): ``outcome`` says
whether one was found (``cause``) or why not (``no_incident``, ``insufficient_evidence``),
``diagnoses`` ranks them, and ``llm_disagreement`` records an LLM explanation that named
another cause (docs/fix-plan-001.md, H72).

Honesty notes:
  * ``confidence_score`` is the score the policy gate used: the top diagnosis's rule score
    (0 for an abstention), read from the gate's edge_log entry — a rule weight, not a
    calibrated probability.
  * ``blast_radius`` is a heuristic over remediation command strings, not a
    rendered-vs-live diff.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    source: str  # event | alert | trace | policy | anchor
    detail: str


class RankedDiagnosis(BaseModel):
    """One structured diagnosis, as ranked by the rules."""
    family: str
    cause: str
    location: str = ""
    score: float = 0.0
    label: str = ""
    rule: str = ""


class RemediationAction(BaseModel):
    commands: list[str] = Field(default_factory=list)


class RollbackAction(BaseModel):
    available: bool = False
    commands: list[str] = Field(default_factory=list)


class PullRequestRef(BaseModel):
    """A draft PR/MR opened for the verdict's fix (PR/MR-first remediation)."""
    url: str
    branch: str = ""
    provider: str = ""            # github | gitlab | gitea
    draft: bool = True
    number: int | None = None


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


def _pull_request(state: dict[str, Any]) -> "PullRequestRef | None":
    """Project a proposed change (PR/MR-first) onto the envelope, if one was opened.

    ``state['proposed_change']`` is the dict returned by
    ``remediation.change_proposer.ProposedChange.to_dict()``. Absent when no draft
    PR/MR was opened for this investigation.
    """
    pc = state.get("proposed_change") or {}
    if not pc.get("url"):
        return None
    return PullRequestRef(
        url=pc["url"],
        branch=pc.get("branch", ""),
        provider=pc.get("provider", ""),
        draft=bool(pc.get("draft", True)),
        number=pc.get("number"),
    )


def _next_steps(policy: str | None, reasons: list[str], diagnosis: dict[str, Any]) -> list[str]:
    outcome = diagnosis.get("outcome")
    if outcome == "no_incident":
        return ["No incident diagnosed: nothing to apply."] + list(reasons)
    if outcome == "insufficient_evidence":
        failed = diagnosis.get("failed_collectors") or []
        first = ("Not enough evidence to name a cause: restore " + ", ".join(failed)
                 + " and investigate again." if failed
                 else "Not enough evidence to name a cause: run the checks, or investigate again "
                      "with more context.")
        return [first] + list(reasons)
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
    outcome: Literal["cause", "no_incident", "insufficient_evidence"] | None = None
    diagnoses: list[RankedDiagnosis] = Field(default_factory=list)
    failed_collectors: list[str] = Field(default_factory=list)
    llm_disagreement: str = ""           # the LLM explanation named another cause
    confidence_label: str = ""           # HIGH | MEDIUM | LOW | "" (no analysis)
    confidence_score: float | None = None
    policy: Literal["AUTO", "HUMAN_REVIEW", "NO_GO"] | None = None
    blast_radius: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None

    evidence: list[EvidenceItem] = Field(default_factory=list)
    remediation: RemediationAction | None = None
    rollback: RollbackAction | None = None
    pull_request: PullRequestRef | None = None
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
        diagnosis = state.get("diagnosis") or report.get("diagnosis") or {}
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
            outcome=(diagnosis.get("outcome")
                     if diagnosis.get("outcome") in ("cause", "no_incident", "insufficient_evidence")
                     else None),
            diagnoses=[RankedDiagnosis(**{k: d[k] for k in RankedDiagnosis.model_fields if k in d})
                       for d in diagnosis.get("diagnoses") or [] if d.get("family") and d.get("cause")],
            failed_collectors=list(diagnosis.get("failed_collectors") or []),
            llm_disagreement=str(state.get("llm_disagreement") or report.get("llm_disagreement") or ""),
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
            pull_request=_pull_request(state),
            next_steps=_next_steps(policy, reasons, diagnosis),
        )
