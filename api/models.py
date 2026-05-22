from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    IDLE             = "IDLE"
    RUNNING          = "RUNNING"
    AWAITING_REVIEW  = "AWAITING_REVIEW"
    COMPLETED        = "COMPLETED"
    FAILED           = "FAILED"


# ── Requests ──────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    query: str
    namespaces: list[str] = Field(default_factory=list)
    kubeconfig: str | None = None
    kube_context: str | None = None

class FeedbackRequest(BaseModel):
    human_decision: str | None = None   # "approve" | "reject"
    extra_context: str | None  = None   # injected when re-running after LOW confidence


# ── Edge log ──────────────────────────────────────────────────────────────────

class EdgeEntry(BaseModel):
    router: str
    edge_taken: str
    reason: str
    snapshot: dict[str, Any]
    ts: str


# ── Patch safety ──────────────────────────────────────────────────────────────

class BlastRadius(BaseModel):
    risk:           str
    summary:        str
    resources:      list[str]
    namespaces:     list[str]
    cluster_scoped: bool = False
    command_count:  int = 0


class IncidentReport(BaseModel):
    """Canonical structured output for a completed RCA."""
    severity:    str
    confidence:  str
    root_cause:  str
    impact:      list[str]
    evidence:    list[str]
    remediation: list[str]
    rollback:    list[str]


# ── State response ────────────────────────────────────────────────────────────

class SessionState(BaseModel):
    session_id: str
    status: SessionStatus
    query: str | None = None
    kube_version: str | None = None
    confidence: str | None = None

    # Reasoning chain
    current_hypothesis: str | None = None
    candidate_paths: list[str] = Field(default_factory=list)
    reasoning_history: list[dict[str, Any]] = Field(default_factory=list)
    hypothesis_sources: list[dict[str, Any]] = Field(default_factory=list)
    path_confidence_history: list[str] = Field(default_factory=list)

    # Edge tracing — WHY each routing decision was made
    edge_log: list[EdgeEntry] = Field(default_factory=list)

    # Ingestion telemetry — KOs per collector
    ingestion_stats: dict[str, Any] = Field(default_factory=dict)

    # Current report
    report: dict[str, Any] | None = None

    # Signal content — full text surfaced from ContextWindow
    events:            list[str] = Field(default_factory=list)
    traces:            list[str] = Field(default_factory=list)
    alerts:            list[str] = Field(default_factory=list)
    anchor_fixes:      list[str] = Field(default_factory=list)
    policy_violations: list[str] = Field(default_factory=list)

    # Suggestions from report (causal chain + remediation)
    causal_chain: list[str] = Field(default_factory=list)
    suggestions:  list[str] = Field(default_factory=list)

    # Dry-run validation
    dry_run_results: list[dict[str, Any]] = Field(default_factory=list)

    # Blast radius (populated before human gate)
    blast_radius: BlastRadius | None = None

    # Canonical incident report (populated when status=COMPLETED or AWAITING_REVIEW)
    incident_report: IncidentReport | None = None

    # Human review payload (only set when status=AWAITING_REVIEW)
    review_payload: dict[str, Any] | None = None

    error: str | None = None


class SessionCreated(BaseModel):
    session_id: str


class HealthResponse(BaseModel):
    status: str = "ok"


# ── Alertmanager webhook ───────────────────────────────────────────────────────

class AlertmanagerAlert(BaseModel):
    status: str                                    # "firing" | "resolved"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str = ""
    endsAt: str = ""
    generatorURL: str = ""
    fingerprint: str = ""


class AlertmanagerPayload(BaseModel):
    version: str = "4"
    groupKey: str = ""
    status: str                                    # "firing" | "resolved"
    receiver: str = ""
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str = ""
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


class WebhookTriggered(BaseModel):
    session_ids: list[str]
    skipped: int = 0
