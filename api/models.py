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
    events:            list[str] = Field(default_factory=list)   # K8s Warning events
    traces:            list[str] = Field(default_factory=list)   # OTel error traces
    alerts:            list[str] = Field(default_factory=list)   # Prometheus firing alerts
    anchor_fixes:      list[str] = Field(default_factory=list)   # helm commands (declared→observed drift)
    policy_violations: list[str] = Field(default_factory=list)   # OPA / Kyverno violations

    # Suggestions from report (causal chain + remediation)
    causal_chain: list[str] = Field(default_factory=list)
    suggestions:  list[str] = Field(default_factory=list)        # remediation commands

    # Dry-run validation
    dry_run_results: list[dict[str, Any]] = Field(default_factory=list)

    # Human review payload (only set when status=AWAITING_REVIEW)
    review_payload: dict[str, Any] | None = None

    error: str | None = None


class SessionCreated(BaseModel):
    session_id: str


class HealthResponse(BaseModel):
    status: str = "ok"
