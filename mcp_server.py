"""
KubeVerdict MCP server — stdio transport.

Exposes three tools consumable by any MCP-compatible client
(Claude Desktop, Cursor, Continue, etc.):

  kube_rca       — full root-cause analysis on a live or offline namespace
  helm_drift     — detect Helm value drift for a release
  blast_radius   — assess risk + rollback availability for remediation commands

Air-gap friendly: all inference runs locally via Ollama (no data leaves the cluster).

Usage (stdio — Claude Desktop / Cursor config):
    {
      "mcpServers": {
        "kube-verdict": {
          "command": "python",
          "args": ["mcp_server.py"],
          "cwd": "/path/to/kube-verdict"
        }
      }
    }

Run standalone (for testing):
    python mcp_server.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

log = logging.getLogger(__name__)

server = Server("kube-verdict")


# ─────────────────────────────────────────────────────────────────────────────
# Tool schemas
# ─────────────────────────────────────────────────────────────────────────────

_TOOLS = [
    types.Tool(
        name="kube_rca",
        description=(
            "Run a full root-cause analysis on a Kubernetes namespace. "
            "Correlates pod events, Helm drift, and anchor violations into a "
            "ranked diagnosis with remediation commands and confidence score. "
            "Works air-gapped (Ollama / Mistral, no external calls)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Incident description or symptom (e.g. 'api pods crashlooping')",
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to investigate (default: all namespaces)",
                },
                "kubeconfig": {
                    "type": "string",
                    "description": "Path to kubeconfig file (default: ~/.kube/config)",
                },
                "kube_context": {
                    "type": "string",
                    "description": "kubeconfig context to use",
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="helm_drift",
        description=(
            "Detect drift between a Helm release's declared values and the "
            "actual running Kubernetes resources. Returns a list of drifted fields "
            "with declared vs observed values."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "release": {
                    "type": "string",
                    "description": "Helm release name",
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace of the release",
                },
                "kubeconfig": {
                    "type": "string",
                    "description": "Path to kubeconfig file (default: ~/.kube/config)",
                },
                "kube_context": {
                    "type": "string",
                    "description": "kubeconfig context to use",
                },
            },
            "required": ["release", "namespace"],
        },
    ),
    types.Tool(
        name="blast_radius",
        description=(
            "Assess the blast radius and rollback availability for a set of "
            "proposed remediation commands before applying them. "
            "Returns risk level (LOW/MEDIUM/HIGH/CRITICAL), affected namespaces, "
            "cluster-scoped flag, and whether a safe rollback path exists."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "remediation_commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "kubectl / helm commands to evaluate",
                },
                "affected_resources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Resources already identified as affected (e.g. ['Pod/api-xyz'])",
                },
                "rollback_commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Known rollback commands; inferred automatically if omitted",
                },
            },
            "required": ["remediation_commands"],
        },
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return _TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        if name == "kube_rca":
            result = await _kube_rca(arguments)
        elif name == "helm_drift":
            result = await _helm_drift(arguments)
        elif name == "blast_radius":
            result = await _blast_radius(arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as exc:
        log.exception("tool %s failed", name)
        result = {"error": str(exc)}

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _kube_rca(args: dict[str, Any]) -> dict[str, Any]:
    """Collect cluster state and run RCAAnalyzer."""
    import config as cfg
    from ingestion import K8sCollector, HelmCollector, HelmDriftDetector
    from llm import build_llm_client
    from rca.analyzer import RCAAnalyzer
    from vectorstore.embedder import Embedder
    from vectorstore.store import FAISSStore

    query        = args["query"]
    namespace    = args.get("namespace")
    kubeconfig   = args.get("kubeconfig") or cfg.KUBECONFIG
    kube_context = args.get("kube_context") or cfg.KUBE_CONTEXT

    namespaces = [namespace] if namespace else cfg.KUBE_NAMESPACES or None

    collector = K8sCollector(kubeconfig=kubeconfig, context=kube_context)
    graph = await asyncio.to_thread(collector.collect, namespaces=namespaces)

    helm = HelmCollector(kubeconfig=kubeconfig, kube_context=kube_context)
    await asyncio.to_thread(helm.collect, graph, namespaces=namespaces)
    await asyncio.to_thread(HelmDriftDetector().detect_all, graph)

    store = FAISSStore(embedder=Embedder())
    await asyncio.to_thread(store.index_graph, graph)

    llm    = build_llm_client()
    report = await asyncio.to_thread(RCAAnalyzer(graph=graph, store=store, llm=llm).analyze, query)

    return {
        "query":        report.query,
        "summary":      report.summary,
        "root_cause":   report.root_cause,
        "causal_chain": report.causal_chain,
        "affected":     report.affected,
        "remediation":  report.remediation,
        "rollback":     report.rollback,
        "confidence":   report.confidence,
        "pre_llm_confidence": (
            {
                "score": report.context.pre_llm_confidence.score,
                "label": report.context.pre_llm_confidence.label,
            }
            if report.context and report.context.pre_llm_confidence else None
        ),
    }


async def _helm_drift(args: dict[str, Any]) -> dict[str, Any]:
    """Run HelmDriftDetector for a specific release."""
    import config as cfg
    from ingestion import K8sCollector, HelmCollector, HelmDriftDetector
    from ontology.entities import ResourceKind
    from ontology.relationships import RelationshipType

    release      = args["release"]
    namespace    = args["namespace"]
    kubeconfig   = args.get("kubeconfig") or cfg.KUBECONFIG
    kube_context = args.get("kube_context") or cfg.KUBE_CONTEXT

    collector = K8sCollector(kubeconfig=kubeconfig, context=kube_context)
    graph = await asyncio.to_thread(collector.collect, namespaces=[namespace])

    helm = HelmCollector(kubeconfig=kubeconfig, kube_context=kube_context)
    await asyncio.to_thread(helm.collect, graph, namespaces=[namespace])
    drift_count = await asyncio.to_thread(HelmDriftDetector().detect_all, graph)

    # Drift annotations live on the drifted workloads (Deployment/StatefulSet/…),
    # which point at the release via a DRIFTS_FROM edge; sub-chart drift is
    # annotated on the release itself. Gather both.
    drift_items: list[dict] = []
    for rel in graph.entities(ResourceKind.HELM_RELEASE):
        if rel.name != release:
            continue
        drifted = graph.neighbors(rel.uid, RelationshipType.DRIFTS_FROM, reverse=True)
        for entity in [rel, *drifted]:
            for key, value in (entity.annotations or {}).items():
                if not key.startswith("drift."):
                    continue
                item = _parse_drift_annotation(key, value)
                if item:
                    item["resource"] = f"{entity.kind.value}/{entity.name}"
                    drift_items.append(item)

    return {
        "release":    release,
        "namespace":  namespace,
        "drift_count": drift_count,
        "drift_items": drift_items,
    }


def _parse_drift_annotation(key: str, value: str) -> dict[str, str] | None:
    """
    Parse a ``drift.<field>`` annotation produced by ``DriftItem.to_text()``:
        "drift field=<fp> declared=<d> observed=<o> severity=<sev>"
    Returns {field, declared, observed, severity} or None if unparseable.
    """
    m = re.search(r"declared=(.*?) observed=(.*) severity=(\S+)$", str(value))
    if not m:
        return None
    return {
        "field":    key[len("drift."):],
        "declared": m.group(1),
        "observed": m.group(2),
        "severity": m.group(3),
    }


async def _blast_radius(args: dict[str, Any]) -> dict[str, Any]:
    """Delegate to remediation.blast_radius.compute_blast_radius."""
    from remediation.blast_radius import compute_blast_radius

    remediation = args["remediation_commands"]
    affected    = args.get("affected_resources", [])
    rollback    = args.get("rollback_commands", [])

    return await asyncio.to_thread(compute_blast_radius, remediation, affected, rollback)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_main())
