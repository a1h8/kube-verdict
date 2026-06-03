"""
Canonical investigation service.

Single entry point for running the KubeVerdict decision pipeline, so every
surface (REST API, MCP tools, future ``/investigate``) produces the **same**
verdict from the same LangGraph workflow — no parallel RCA paths.

``run_investigation`` is **proposal-only**: it runs the graph up to the policy
verdict and returns the decision, but never crosses into dry-run/remediation —
it stops the moment a ``verdict`` is produced. Execution stays a separate,
human-gated concern (see the REST ``/sessions`` flow with its approval gate).
"""
from __future__ import annotations

import uuid
from typing import Any

from workflow.graph import build_graph

# Same compiled graph (in-memory checkpointer) the REST API uses.
_graph = build_graph()


async def run_investigation(
    *,
    query: str,
    namespaces: list[str] | None = None,
    kubeconfig: str | None = None,
    kube_context: str | None = None,
    store: Any | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Run the investigation graph to its decision point and return the final state.

    Returns the workflow state dict, which includes ``report_dict``, ``verdict``,
    ``verdict_reasons`` and ``blast_radius``. Proposal-only: streaming stops as
    soon as the policy gate has produced a ``verdict``, before any dry-run or
    remediation node runs.
    """
    configurable: dict[str, Any] = {"thread_id": thread_id or uuid.uuid4().hex}
    if store is not None:
        configurable["store"] = store
    cfg = {"configurable": configurable}

    initial_state = {
        "query":        query,
        "namespaces":   namespaces or [],
        "kubeconfig":   kubeconfig,
        "kube_context": kube_context,
        "edge_log":     [],
    }

    final: dict[str, Any] = {}
    async for state in _graph.astream(initial_state, cfg, stream_mode="values"):
        final = dict(state)
        # Stop at the decision point — never proceed to dry-run / remediation.
        if final.get("verdict"):
            break
    return final


def verdict_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Project a workflow state onto the canonical proposal shape returned to callers."""
    report = state.get("report_dict") or {}
    return {
        "query":              report.get("query", ""),
        "summary":            report.get("summary", ""),
        "root_cause":         report.get("root_cause", ""),
        "causal_chain":       report.get("causal_chain", []),
        "affected":           report.get("affected", []),
        "remediation":        report.get("remediation", []),
        "rollback":           report.get("rollback", []),
        "confidence":         report.get("confidence", ""),
        "pre_llm_confidence": report.get("pre_llm_confidence"),
        "verdict":            state.get("verdict"),
        "verdict_reasons":    state.get("verdict_reasons") or [],
        "blast_radius":       state.get("blast_radius"),
    }
