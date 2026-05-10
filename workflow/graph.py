"""
KubeWhisperer LangGraph workflow.

Graph topology
──────────────

    START
      │
    ingest          (K8s API + Helm + Helmfile → OntologyGraph)
      │
    index           (embed entities → FAISSStore)
      │
    analyze  ◄──────────────────────────────────────┐
      │                                              │ retry (LOW confidence,
    ── confidence_router ──                          │       < 2 attempts)
         │           │                              │
       "review"   "retry" ───── increment_retry ────┘
         │
    human_review    [INTERRUPT — waits for operator decision]
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

    # Run until the human interrupt
    for event in graph.stream({"query": "pods crashlooping"}, config=config):
        if "__interrupt__" in event:
            payload = event["__interrupt__"][0].value
            print(payload["summary"])
            print("Remediation commands:")
            for cmd in payload["remediation"]:
                print(f"  $ {cmd}")

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
    confidence_router,
    gitops_node,
    human_review_node,
    human_router,
    index_node,
    ingest_node,
    prometheus_node,
    remediation_node,
    signal_analysis_node,
)
from workflow.state import RCAState

log = logging.getLogger(__name__)

# Exported type alias so callers can annotate without importing StateGraph
RCAGraph = StateGraph


def _increment_retry(state: RCAState) -> dict:
    """Bump retry counter before looping back to analyze."""
    return {"retry_count": (state.get("retry_count") or 0) + 1}


def build_graph(checkpointer=None) -> StateGraph:
    """
    Assemble and compile the RCA workflow graph.

    Parameters
    ----------
    checkpointer:
        LangGraph checkpointer backend.  Defaults to in-memory (MemorySaver).
        Pass a SqliteSaver or PostgresSaver for persistence across restarts.

    Returns
    -------
    Compiled LangGraph CompiledGraph ready for `.invoke()` / `.stream()`.
    """
    builder = StateGraph(RCAState)

    # ── Nodes ────────────────────────────────────────────────────────────────
    builder.add_node("ingest",           ingest_node)
    builder.add_node("prometheus",       prometheus_node)
    builder.add_node("gitops",           gitops_node)
    builder.add_node("index",            index_node)
    builder.add_node("signal_analysis",  signal_analysis_node)
    builder.add_node("analyze",          analyze_node)
    builder.add_node("increment_retry",  _increment_retry)
    builder.add_node("human_review",     human_review_node)
    builder.add_node("remediation",      remediation_node)

    # ── Edges: linear spine ─────────────────────────────────────────────────
    builder.add_edge(START,              "ingest")
    builder.add_edge("ingest",           "prometheus")
    builder.add_edge("prometheus",       "gitops")
    builder.add_edge("gitops",           "index")
    builder.add_edge("index",            "signal_analysis")
    builder.add_edge("signal_analysis",  "analyze")

    # ── Conditional: after analysis → retry or human review ─────────────────
    builder.add_conditional_edges(
        "analyze",
        confidence_router,
        {
            "retry":  "increment_retry",   # LOW confidence → widen context
            "review": "human_review",      # good enough → human gate
        },
    )

    # ── Retry loop: increment counter then back to analyze ───────────────────
    builder.add_edge("increment_retry", "analyze")

    # ── Conditional: human decision → remediation or stop ───────────────────
    builder.add_conditional_edges(
        "human_review",
        human_router,
        {
            "approve": "remediation",
            "reject":  END,
        },
    )

    builder.add_edge("remediation", END)

    # ── Compile ──────────────────────────────────────────────────────────────
    cp = checkpointer if checkpointer is not None else MemorySaver()
    return builder.compile(checkpointer=cp)
