"""
KubeVerdict MCP server — stdio transport.

Exposes tools consumable by any MCP-compatible client
(Claude Desktop, Cursor, Continue, etc.):

  kube_rca              — full root-cause analysis on a live or offline namespace
  helm_drift            — detect Helm value drift for a release (mode-aware)
  expected_state_drift  — diff a pushed expected-state source (Helm / Helmfile /
                          Kustomize / raw manifests) at a pinned version vs live
  blast_radius          — assess risk + rollback availability for remediation commands

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
            "with declared vs observed values. Mode-aware: if an expected-state "
            "source has been pushed for the release (Helm / Helmfile / Kustomize / "
            "raw manifests), it is also rendered at its pinned version and diffed "
            "against live — see also the expected_state_drift tool."
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
                "chart": {
                    "type": "string",
                    "description": "Pushed expected-state source name (default: release name)",
                },
                "chart_version": {
                    "type": "string",
                    "description": "Pinned version of the pushed source (default: latest pushed)",
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
    types.Tool(
        name="expected_state_drift",
        description=(
            "Detect drift between the EXPECTED state — rendered from a pushed "
            "enterprise source at a pinned version — and the live Kubernetes "
            "resources. Deployment-mode agnostic: the source may be a Helm chart, "
            "a Helmfile bundle, a Kustomize overlay, or raw/rendered manifests "
            "(Jsonnet/Tanka, CDK8s, ArgoCD/Flux output). Returns drifted fields "
            "with declared (rendered) vs observed values. The version is evidence: "
            "a different version renders a different expected baseline."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "chart": {
                    "type": "string",
                    "description": "Pushed expected-state source name (in the chart store)",
                },
                "version": {
                    "type": "string",
                    "description": "Pinned source version (default: latest pushed)",
                },
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace to compare against",
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
            "required": ["chart", "namespace"],
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
async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    handler = _resolve_handler(name)
    if handler is None:
        return _error_result(f"Unknown tool: {name}")

    import config as cfg

    try:
        result = await asyncio.wait_for(handler(arguments), timeout=cfg.MCP_TOOL_TIMEOUT)
    except asyncio.TimeoutError:
        # Bound the wall-clock so a slow collect/LLM never hangs the agent.
        log.warning("tool %s timed out after %ds", name, cfg.MCP_TOOL_TIMEOUT)
        return _error_result(f"{name} timed out after {cfg.MCP_TOOL_TIMEOUT}s")
    except Exception as exc:
        # Surface the failure as a tool error (isError=True) so the calling
        # agent can distinguish it from a normal result — not a 200-OK payload
        # that merely happens to contain an "error" key.
        log.exception("tool %s failed", name)
        return _error_result(str(exc))

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result, indent=2))],
    )


def _error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps({"error": message}, indent=2))],
        isError=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _kube_rca(args: dict[str, Any]) -> dict[str, Any]:
    """Run the canonical investigation pipeline (same graph as the REST API).

    Routes through services.investigation_service so MCP, the API and any future
    surface return the same verdict. Proposal-only: returns the policy decision
    (root cause + blast radius + verdict), never executes remediation.
    """
    import config as cfg
    from services.investigation_service import run_investigation, verdict_summary

    query        = args["query"]
    namespace    = args.get("namespace")
    kubeconfig   = args.get("kubeconfig") or cfg.KUBECONFIG
    kube_context = args.get("kube_context") or cfg.KUBE_CONTEXT

    namespaces = [namespace] if namespace else (cfg.KUBE_NAMESPACES or [])

    state = await run_investigation(
        query=query,
        namespaces=namespaces,
        kubeconfig=kubeconfig,
        kube_context=kube_context,
    )
    return verdict_summary(state)


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
                    item["source"]   = "helm"
                    drift_items.append(item)

    # Mode-aware: if an expected-state source was pushed for this release, also
    # render it (Helm / Helmfile / Kustomize / manifests) and diff against live.
    expected_mode = None
    try:
        chart, rendered_items = await asyncio.to_thread(
            _rendered_expected_drift, graph,
            args.get("chart") or release, args.get("chart_version"), namespace,
        )
        if chart is not None:
            expected_mode = chart.render_type
            drift_items.extend(rendered_items)
    except Exception as exc:  # never fail the helm path on an expected-state issue
        log.warning("helm_drift: expected-state diff skipped: %s", exc)

    return {
        "release":             release,
        "namespace":           namespace,
        "drift_count":         drift_count,
        "drift_items":         drift_items,
        "expected_state_mode": expected_mode,
    }


def _rendered_expected_drift(graph, chart_name: str, version, namespace: str,
                             chart_store=None):
    """
    Render a pushed expected-state source at its pinned version and diff it
    against the live graph. Mode-agnostic (Helm / Helmfile / Kustomize /
    manifests). Returns (EnterpriseChart | None, [drift_item, …]); the chart is
    None when nothing has been pushed for ``chart_name``.
    """
    from ingestion.manifest_differ import ManifestDiffer
    from knowledge.chart_indexer import ChartIndexer
    from knowledge.chart_store import ChartStore

    cs = chart_store or ChartStore()
    if version is None:
        versions = cs.versions(chart_name)
        version = versions[-1] if versions else None
    if version is None:
        return None, []
    chart = cs.get(chart_name, version)
    if chart is None:
        return None, []

    rendered = ChartIndexer(None).render(cs, chart, namespace=namespace)
    drifts = ManifestDiffer().diff(rendered, graph)
    items = [
        {
            "field":    d.field_path,
            "declared": str(d.declared),
            "observed": str(d.observed),
            "severity": d.severity,
            "source":   "rendered",
            "chart":    f"{chart.name}@{chart.version}",
            "mode":     chart.render_type,
        }
        for d in drifts
    ]
    return chart, items


async def _expected_state_drift(args: dict[str, Any]) -> dict[str, Any]:
    """Diff a pushed expected-state source (any deploy mode) against live cluster."""
    import config as cfg
    from ingestion import K8sCollector

    chart_name   = args["chart"]
    namespace    = args["namespace"]
    version      = args.get("version")
    kubeconfig   = args.get("kubeconfig") or cfg.KUBECONFIG
    kube_context = args.get("kube_context") or cfg.KUBE_CONTEXT

    collector = K8sCollector(kubeconfig=kubeconfig, context=kube_context)
    graph = await asyncio.to_thread(collector.collect, namespaces=[namespace])

    chart, items = await asyncio.to_thread(
        _rendered_expected_drift, graph, chart_name, version, namespace,
    )
    if chart is None:
        return {
            "chart":       chart_name,
            "namespace":   namespace,
            "error":       f"no expected-state source pushed for '{chart_name}'",
            "drift_items": [],
        }
    return {
        "chart":       f"{chart.name}@{chart.version}",
        "mode":        chart.render_type,
        "namespace":   namespace,
        "drift_count": len(items),
        "drift_items": items,
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


# Single registry, shared by the MCP call_tool handler and the OpenAI adapter.
# Maps tool name → implementation attribute; resolved at call time so the
# functions stay patchable and there is one authoritative tool list.
_HANDLERS = {
    "kube_rca": "_kube_rca",
    "helm_drift": "_helm_drift",
    "expected_state_drift": "_expected_state_drift",
    "blast_radius": "_blast_radius",
}


def _resolve_handler(name: str):
    attr = _HANDLERS.get(name)
    return globals().get(attr) if attr else None


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI function-calling adapter
# ─────────────────────────────────────────────────────────────────────────────

async def dispatch_openai_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """
    Execute one OpenAI-style tool call against the same handlers the MCP server
    uses, so any function-calling framework (OpenAI SDK, LangChain, LlamaIndex)
    can drive the three tools without speaking MCP.

    Accepts the OpenAI ``tool_calls[i]`` shape::

        {"id": "...", "type": "function",
         "function": {"name": "helm_drift", "arguments": "{\\"release\\": ...}"}}

    ``arguments`` may be a JSON string (as OpenAI sends it) or an already-parsed
    dict. Returns the ``tool`` message to append to the conversation::

        {"role": "tool", "tool_call_id": "...", "name": "...", "content": "<json>"}

    A failed or unknown tool yields ``content`` of ``{"error": ...}`` — the
    caller decides whether to retry or surface it.
    """
    fn = tool_call.get("function", {})
    name = fn.get("name", "")
    raw_args = fn.get("arguments", {})
    call_id = tool_call.get("id", "")

    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
    except (json.JSONDecodeError, TypeError) as exc:
        return _openai_tool_message(call_id, name, {"error": f"invalid arguments: {exc}"})

    handler = _resolve_handler(name)
    if handler is None:
        return _openai_tool_message(call_id, name, {"error": f"Unknown tool: {name}"})

    import config as cfg

    try:
        result = await asyncio.wait_for(handler(args), timeout=cfg.MCP_TOOL_TIMEOUT)
    except asyncio.TimeoutError:
        result = {"error": f"{name} timed out after {cfg.MCP_TOOL_TIMEOUT}s"}
    except Exception as exc:
        log.exception("openai tool %s failed", name)
        result = {"error": str(exc)}

    return _openai_tool_message(call_id, name, result)


def _openai_tool_message(call_id: str, name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": json.dumps(result, indent=2),
    }


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
