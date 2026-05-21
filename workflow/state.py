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

    # ── Ingestion telemetry ───────────────────────────────────────────────────
    ingestion_stats: dict[str, Any]  # per-step stats + fallbacks, written by nodes

    # ── Multi-path reasoning ─────────────────────────────────────────────────
    candidate_paths: list[str]    # remaining hypotheses to explore (popped FIFO)
    current_hypothesis: str       # hypothesis under analysis ("" = use raw query)
    reasoning_history: list[dict] # [{step, hypothesis, confidence, summary, report_dict, retry_count}]
    hypothesis_sources: list[dict] # rule-based evidence that grounded each hypothesis
    path_confidence_history: list[str]  # confidence sequence for current path ["LOW","LOW",…]

    # ── Blast radius ─────────────────────────────────────────────────────────
    blast_radius: dict[str, Any]  # {risk, summary, resources, namespaces, cluster_scoped, command_count}

    # ── Dry-run validation ────────────────────────────────────────────────────
    dry_run_results: list[dict]   # [{original_cmd, dry_cmd, output, exit_code}]

    # ── Example matching ─────────────────────────────────────────────────────
    example_match: bool          # True when example_lookup found a strong match
    matched_example_id: str      # UID of matched example (e.g. "example:abc123")

    # ── Edge tracing ──────────────────────────────────────────────────────────
    edge_log: list[dict]        # [{router, edge_taken, reason, snapshot, ts}]
    _confidence_edge: str       # internal: pre-computed edge for confidence_router
    _human_edge: str            # internal: pre-computed edge for human_router

    # ── Control flow ──────────────────────────────────────────────────────────
    retry_count: int            # how many times analyze has been retried on current path
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
