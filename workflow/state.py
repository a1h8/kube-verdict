from __future__ import annotations
from typing import Any, TypedDict


class RCAState(TypedDict, total=False):
    """
    Serialisable workflow state — only primitive / JSON-safe types here.

    Heavy objects (OntologyGraph, FAISSStore) are NOT in state; they are passed
    via config["configurable"] so LangGraph never tries to checkpoint them.
    """
    # ── Inputs ────────────────────────────────────────────────────────────────
    query: str                  # incident description
    kubeconfig: str | None
    kube_context: str | None
    namespaces: list[str]

    # ── Analysis outputs ──────────────────────────────────────────────────────
    raw_analysis: str
    kube_version: str
    confidence: str             # "LOW" | "MEDIUM" | "HIGH" | ""

    # ── Structured report (stored as plain dict for serialisability) ──────────
    report_dict: dict[str, Any]  # RCAReport.to_dict()

    # ── Control flow ──────────────────────────────────────────────────────────
    retry_count: int            # how many times analyze has been retried
    human_decision: str         # "approve" | "reject" | ""
    error: str


class WorkflowConfig(TypedDict, total=False):
    """
    Runtime-injected dependencies (never checkpointed).
    Passed via config["configurable"] at invoke / stream time.
    """
    thread_id: str
    graph: Any          # OntologyGraph  — pre-built or built by ingest_node
    store: Any          # FAISSStore     — pre-built or built by index_node
    llm: Any            # OllamaClient   — optional override (for tests)
