"""Formal blast-radius / patch-safety model.

Wraps ``remediation.blast_radius.compute_blast_radius`` in a typed object so the
risk assessment is part of the canonical :class:`IncidentReport` contract rather
than an inline dict passed around by convention.

Honesty note: the underlying computation is a heuristic over remediation command
strings (verb / namespace / kind / cluster-scope / affected-count), not a
rendered-vs-live diff. See ``remediation/blast_radius.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from remediation.blast_radius import compute_blast_radius

Risk = str  # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"


@dataclass
class BlastRadius:
    risk: Risk = "LOW"
    summary: str = ""
    resources: list[str] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)
    cluster_scoped: bool = False
    command_count: int = 0
    rollback_available: bool = True

    @classmethod
    def from_remediation(
        cls,
        remediation: list[str],
        affected: list[str],
        rollback_cmds: list[str],
    ) -> "BlastRadius":
        return cls.from_dict(
            compute_blast_radius(
                list(remediation or []),
                list(affected or []),
                list(rollback_cmds or []),
            )
        )

    @classmethod
    def from_dict(cls, d: dict) -> "BlastRadius":
        return cls(
            risk=d.get("risk", "LOW"),
            summary=d.get("summary", ""),
            resources=list(d.get("resources") or []),
            namespaces=list(d.get("namespaces") or []),
            cluster_scoped=bool(d.get("cluster_scoped", False)),
            command_count=int(d.get("command_count", 0)),
            rollback_available=bool(d.get("rollback_available", True)),
        )

    def to_dict(self) -> dict:
        return {
            "risk": self.risk,
            "summary": self.summary,
            "resources": self.resources,
            "namespaces": self.namespaces,
            "cluster_scoped": self.cluster_scoped,
            "command_count": self.command_count,
            "rollback_available": self.rollback_available,
        }
