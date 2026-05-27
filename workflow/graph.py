"""
KubeVerdict LangGraph workflow.

Graph topology
──────────────

    START
      │
    ingest          (K8s API + Helm + Helmfile → OntologyGraph)
      │
    metrics         (metrics-server CPU/memory → pod annotations)
      │
    prometheus      (alert correlation)
      │
    otel            (traces + logs enrichment)
      │
    gitops          (GitOps drift detection — helm template + ManifestDiffer)
      │
    anchor          (declared-value anchors — K8s schema + rendered manifests)
      │
    index           (embed entities → FAISSStore)
      │
    signal_analysis (PatchTST anomaly detection)
      │
    hypothesize     (extract candidate hypotheses from unhealthy graph signals)
      │
    analyze  ◄──────────────────────────────────────────────┐
      │                                                     │
    ── confidence_router ─────────────────────────────────  │
         │           │               │                      │
       "review"  "retry"         "next_path"                │
         │        (LOW,            (LOW,                    │
         │        retries          retries                  │
         │        < MAX)           exhausted,               │
         │           │             more candidates)         │
         │     increment_retry ────────────────────────┘    │
         │                         │                        │
         │                   archive_path ──────────────────┘
         │                   (save to history,
         │                    pop next hypothesis)
         │
    select_best     (pick highest-confidence path from history)
         │
    blast_radius    (risk scoring + rollback plan)
         │
    monte_carlo     (200-sim stability: win_rate ≥ 0.80 = stable)
         │
    log_policy_decision  (policy_gate: AUTO / HUMAN_REVIEW / NO_GO)
         │
    ── verdict_router ──────────────────────────────
         │               │                   │
       "auto"      "human_review"          "no_go"
         │               │                   │
    dry_run         dry_run                 END
         │               │
    remediation   log_human_decision
         │               │
        END          human_review   [INTERRUPT]
                         │
                    ── human_router ──
                         │           │
                      "approve"   "reject"
                         │           │
                    remediation    END
                         │
                        END

Usage
─────
    from workflow.graph import build_graph
    from langgraph.types import Command

    graph = build_graph()
    config = {"configurable": {"thread_id": "incident-001"}}

    # Stream until the human interrupt (HUMAN_REVIEW path only)
    for event in graph.stream({"query": "pods crashlooping"}, config=config):
        if "__interrupt__" in event:
            payload = event["__interrupt__"][0].value
            print(f"Verdict: {payload['verdict']}")
            print("Best analysis:", payload["summary"])

    # Human approves or rejects
    decision = input("Approve? [approve/reject]: ").strip().lower()
    final_state = graph.invoke(Command(resume=decision), config=config)
"""
from __future__ import annotations
import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from workflow.nodes import (
    analyze_node,
    anchor_node,
    archive_path_node,
    blast_radius_node,
    confidence_router,
    dry_run_node,
    example_lookup_node,
    example_router,
    gitops_node,
    human_review_node,
    human_router,
    hypothesize_node,
    index_node,
    ingest_node,
    log_confidence_decision_node,
    log_human_decision_node,
    log_policy_decision_node,
    metrics_node,
    monte_carlo_node,
    otel_node,
    prometheus_node,
    remediation_node,
    save_example_node,
    select_best_node,
    signal_analysis_node,
    verdict_router,
)
from workflow.state import RCAState

log = logging.getLogger(__name__)

RCAGraph = StateGraph


def _increment_retry(state: RCAState) -> dict:
    return {"retry_count": (state.get("retry_count") or 0) + 1}


def build_graph(checkpointer=None) -> StateGraph:
    """
    Assemble and compile the RCA workflow graph.

    Parameters
    ----------
    checkpointer:
        LangGraph checkpointer backend.  Defaults to in-memory (MemorySaver).
        Pass a SqliteSaver or PostgresSaver for persistence across restarts.
    """
    builder = StateGraph(RCAState)

    # ── Nodes ────────────────────────────────────────────────────────────────
    builder.add_node("ingest",           ingest_node)
    builder.add_node("metrics",          metrics_node)
    builder.add_node("prometheus",       prometheus_node)
    builder.add_node("otel",             otel_node)
    builder.add_node("gitops",           gitops_node)
    builder.add_node("anchor",           anchor_node)
    builder.add_node("index",            index_node)
    builder.add_node("signal_analysis",  signal_analysis_node)
    builder.add_node("hypothesize",      hypothesize_node)
    builder.add_node("analyze",          analyze_node)
    builder.add_node("increment_retry",  _increment_retry)
    builder.add_node("archive_path",     archive_path_node)
    builder.add_node("select_best",      select_best_node)
    builder.add_node("blast_radius",     blast_radius_node)
    builder.add_node("monte_carlo",      monte_carlo_node)
    builder.add_node("log_policy_decision",   log_policy_decision_node)
    builder.add_node("dry_run",          dry_run_node)
    builder.add_node("human_review",             human_review_node)
    builder.add_node("log_confidence_decision",  log_confidence_decision_node)
    builder.add_node("log_human_decision",       log_human_decision_node)
    builder.add_node("remediation",              remediation_node)
    builder.add_node("example_lookup",   example_lookup_node)
    builder.add_node("save_example",     save_example_node)

    # ── Ingestion spine ──────────────────────────────────────────────────────
    builder.add_edge(START,             "ingest")
    builder.add_edge("ingest",          "metrics")
    builder.add_edge("metrics",         "prometheus")
    builder.add_edge("prometheus",      "otel")
    builder.add_edge("otel",            "gitops")
    builder.add_edge("gitops",          "anchor")
    builder.add_edge("anchor",          "index")
    builder.add_edge("index",           "signal_analysis")
    builder.add_edge("signal_analysis", "hypothesize")
    builder.add_edge("hypothesize",     "example_lookup")
    builder.add_conditional_edges(
        "example_lookup",
        example_router,
        {
            "analyze": "analyze",
            "skip":    "select_best",
        },
    )

    # ── Analysis loop with multi-path fallback ───────────────────────────────
    builder.add_edge("analyze", "log_confidence_decision")
    builder.add_conditional_edges(
        "log_confidence_decision",
        confidence_router,
        {
            "retry":     "increment_retry",   # same hypothesis, wider BFS
            "next_path": "archive_path",       # hypothesis dead-end → try next
            "review":    "select_best",        # converged → pick best path
        },
    )
    builder.add_edge("increment_retry", "analyze")
    builder.add_edge("archive_path",    "analyze")

    # ── Decision Engine: blast radius → MC stability → policy gate ───────────
    builder.add_edge("select_best",   "blast_radius")
    builder.add_edge("blast_radius",  "monte_carlo")
    builder.add_edge("monte_carlo",   "log_policy_decision")
    builder.add_conditional_edges(
        "log_policy_decision",
        verdict_router,
        {
            "auto":         "dry_run",             # skip human gate
            "human_review": "dry_run",             # operator must approve
            "no_go":        END,                   # blocked — no action taken
        },
    )

    # ── AUTO path: dry-run → remediation → done ──────────────────────────────
    # ── HUMAN_REVIEW path: dry-run → human gate ──────────────────────────────
    # Both paths share dry_run; downstream routing reads _verdict_edge.
    builder.add_conditional_edges(
        "dry_run",
        verdict_router,
        {
            "auto":         "remediation",
            "human_review": "log_human_decision",
            "no_go":        END,                   # safety net (already gated above)
        },
    )
    builder.add_edge("log_human_decision", "human_review")
    builder.add_conditional_edges(
        "human_review",
        human_router,
        {
            "approve": "remediation",
            "reject":  END,
        },
    )
    builder.add_edge("remediation", "save_example")
    builder.add_edge("save_example", END)

    # ── Compile ──────────────────────────────────────────────────────────────
    cp = checkpointer if checkpointer is not None else MemorySaver()
    return builder.compile(checkpointer=cp)
