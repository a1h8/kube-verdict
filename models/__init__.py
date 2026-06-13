"""Canonical contract models — the stable shapes every consumer can rely on.

  * :class:`IncidentReport` — the full investigation projection
  * :class:`Decision`       — policy-gate verdict (AUTO / HUMAN_REVIEW / NO_GO)
  * :class:`BlastRadius`    — patch-safety / remediation-impact estimate
"""
from models.decision import Decision
from models.incident_report import IncidentReport
from models.patch_safety import BlastRadius

__all__ = ["IncidentReport", "Decision", "BlastRadius"]
