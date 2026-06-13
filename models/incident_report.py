"""Canonical IncidentReport — the contract-first projection of an investigation.

Composes the diagnosis (an :class:`rca.analyzer.RCAReport`) with the formal
blast-radius (:class:`BlastRadius`) and the policy-gate :class:`Decision`, so every
scenario emits **one stable JSON shape**:

    summary, root_cause, confidence{label,score}, evidence[],
    reasoning_paths[], remediation[], rollback_plan{available,commands},
    blast_radius{...}, decision{verdict,reasons,...}

``from_rca`` is duck-typed over the report (it only reads attributes), so it works
for the real ``RCAReport`` and for lightweight stubs in tests without constructing
a full ContextWindow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from models.decision import Decision
from models.patch_safety import BlastRadius

if TYPE_CHECKING:  # avoid an import cycle (rca.analyzer imports this module)
    from rca.analyzer import RCAReport

# diagnosis label → deterministic gate score (mirrors api.verdict_contract)
_LABEL_SCORE = {"HIGH": 0.85, "MEDIUM": 0.65, "LOW": 0.62}
_EVIDENCE_KEYS = (
    ("event", "events"),
    ("alert", "alerts"),
    ("trace", "traces"),
    ("policy", "policy_violations"),
    ("anchor", "anchor_fixes"),
)


@dataclass
class IncidentReport:
    summary: str = ""
    root_cause: str = ""
    confidence_label: str = ""
    confidence_score: float = 0.0
    evidence: list[dict] = field(default_factory=list)
    reasoning_paths: list[str] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    rollback: list[str] = field(default_factory=list)
    blast_radius: BlastRadius = field(default_factory=BlastRadius)
    decision: Decision = field(default_factory=Decision)
    namespace: str = ""
    query: str = ""

    @classmethod
    def from_rca(
        cls,
        report: "RCAReport",
        *,
        namespace: str = "",
        mc_win_rate: float = 1.0,
        max_switches_reached: bool = False,
    ) -> "IncidentReport":
        remediation = list(getattr(report, "remediation", None) or [])
        affected = list(getattr(report, "affected", None) or [])
        rollback = list(getattr(report, "rollback", None) or [])

        br = BlastRadius.from_remediation(remediation, affected, rollback)

        # Confidence score: prefer the deterministic pre-LLM score; fall back to
        # the label → score map rather than fabricating a probability.
        ctx = getattr(report, "context", None)
        pre = getattr(ctx, "pre_llm_confidence", None) if ctx is not None else None
        label = getattr(report, "confidence", "") or (getattr(pre, "label", "") if pre else "")
        pre_score = getattr(pre, "score", None) if pre else None
        score = float(pre_score) if pre_score is not None else _LABEL_SCORE.get((label or "").upper(), 0.20)

        ns = namespace or (br.namespaces[0] if br.namespaces else "")
        decision = Decision.evaluate(
            score=score,
            risk=br.risk,
            rollback_available=br.rollback_available,
            namespace=ns,
            mc_win_rate=mc_win_rate,
            max_switches_reached=max_switches_reached,
        )

        return cls(
            summary=getattr(report, "summary", "") or "",
            root_cause=getattr(report, "root_cause", "") or "",
            confidence_label=label,
            confidence_score=score,
            evidence=cls._evidence(report),
            reasoning_paths=list(getattr(report, "causal_chain", None) or []),
            remediation=remediation,
            rollback=rollback,
            blast_radius=br,
            decision=decision,
            namespace=ns,
            query=getattr(report, "query", "") or "",
        )

    @staticmethod
    def _evidence(report: "RCAReport") -> list[dict]:
        ctx = getattr(report, "context", None)
        src = ctx if ctx is not None else report
        items: list[dict] = []
        for source, key in _EVIDENCE_KEYS:
            for detail in getattr(src, key, None) or []:
                items.append({"source": source, "detail": str(detail)})
        return items

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "root_cause": self.root_cause,
            "confidence": {"label": self.confidence_label, "score": round(self.confidence_score, 4)},
            "evidence": self.evidence,
            "reasoning_paths": self.reasoning_paths,
            "remediation": self.remediation,
            "rollback_plan": {"available": self.blast_radius.rollback_available, "commands": self.rollback},
            "blast_radius": self.blast_radius.to_dict(),
            "decision": self.decision.to_dict(),
            "namespace": self.namespace,
            "query": self.query,
        }
