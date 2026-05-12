"""
KubeWhisperer LangGraph workflow.

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
    dry_run         (execute each remediation command with --dry-run / helm diff)
         │
    human_review    [INTERRUPT — operator sees dry-run output before deciding]
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

    # Stream until the human interrupt
    for event in graph.stream({"query": "pods crashlooping"}, config=config):
        if "__interrupt__" in event:
            payload = event["__interrupt__"][0].value
            print(f"Paths explored: {payload['paths_explored']}")
            for entry in payload["reasoning_history"]:
                print(f"  Path {entry['step']}: {entry['hypothesis']} → {entry['confidence']}")
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
    metrics_node,
    otel_node,
    prometheus_node,
    remediation_node,
    save_example_node,
    select_best_node,
    signal_analysis_node,
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
    builder.add_node("dry_run",          dry_run_node)
    builder.add_node("human_review",     human_review_node)
    builder.add_node("remediation",      remediation_node)
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
    builder.add_conditional_edges(
        "analyze",
        confidence_router,
        {
            "retry":     "increment_retry",   # same hypothesis, wider BFS
            "next_path": "archive_path",       # hypothesis dead-end → try next
            "review":    "select_best",        # converged → pick best path
        },
    )
    builder.add_edge("increment_retry", "analyze")
    builder.add_edge("archive_path",    "analyze")

    # ── Dry-run then human gate ───────────────────────────────────────────────
    builder.add_edge("select_best", "dry_run")
    builder.add_edge("dry_run",     "human_review")
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
