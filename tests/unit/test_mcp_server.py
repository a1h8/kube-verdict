"""
Unit tests for the MCP server layer (mcp_server.py).

These cover the MCP-specific surface — tool listing, call_tool dispatch and
error handling, the drift-annotation parser, and the _helm_drift / _blast_radius
argument mapping. The underlying engines (RCAAnalyzer, HelmDriftDetector,
compute_blast_radius) are tested elsewhere and mocked out here.
"""
import asyncio
import json
from unittest.mock import MagicMock, patch

import mcp_server as m
from ontology.entities import Deployment, DriftItem, HelmRelease
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType


def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# list_tools / schemas
# ─────────────────────────────────────────────────────────────────────────────

class TestListTools:
    def test_exposes_expected_tools(self):
        tools = _run(m.list_tools())
        assert {t.name for t in tools} == {
            "kube_rca", "helm_drift", "expected_state_drift", "blast_radius",
        }

    def test_required_fields_declared(self):
        by_name = {t.name: t for t in _run(m.list_tools())}
        assert by_name["kube_rca"].inputSchema["required"] == ["query"]
        assert by_name["helm_drift"].inputSchema["required"] == ["release", "namespace"]
        assert by_name["expected_state_drift"].inputSchema["required"] == ["chart", "namespace"]
        assert by_name["blast_radius"].inputSchema["required"] == ["remediation_commands"]


# ─────────────────────────────────────────────────────────────────────────────
# call_tool dispatch + error handling
# ─────────────────────────────────────────────────────────────────────────────

class TestCallToolDispatch:
    def test_unknown_tool_flagged_as_error(self):
        out = _run(m.call_tool("does_not_exist", {}))
        assert out.isError is True
        payload = json.loads(out.content[0].text)
        assert "Unknown tool" in payload["error"]

    def test_exception_flagged_as_error(self):
        with patch.object(m, "_blast_radius", side_effect=RuntimeError("boom")):
            out = _run(m.call_tool("blast_radius", {"remediation_commands": []}))
        # The failure must be a tool error, not a 200-OK payload with an error key.
        assert out.isError is True
        payload = json.loads(out.content[0].text)
        assert payload["error"] == "boom"

    def test_success_not_flagged_as_error(self):
        with patch.object(m, "_blast_radius", return_value={"risk": "LOW"}):
            out = _run(m.call_tool("blast_radius", {"remediation_commands": []}))
        assert not out.isError
        assert out.content[0].type == "text"
        assert json.loads(out.content[0].text) == {"risk": "LOW"}

    def test_timeout_flagged_as_error(self):
        async def _slow(_args):
            await asyncio.sleep(1)
            return {}

        import config as cfg
        with patch.object(cfg, "MCP_TOOL_TIMEOUT", 0), \
                patch.object(m, "_blast_radius", _slow):
            out = _run(m.call_tool("blast_radius", {"remediation_commands": []}))
        assert out.isError is True
        payload = json.loads(out.content[0].text)
        assert "timed out" in payload["error"]


# ─────────────────────────────────────────────────────────────────────────────
# _parse_drift_annotation
# ─────────────────────────────────────────────────────────────────────────────

class TestParseDriftAnnotation:
    def test_parses_real_drift_item_output(self):
        d = DriftItem(field_path="spec.replicas", declared=3, observed=1, severity="warning")
        item = m._parse_drift_annotation("drift.spec.replicas", d.to_text())
        assert item == {
            "field": "spec.replicas",
            "declared": "3",
            "observed": "1",
            "severity": "warning",
        }

    def test_preserves_dotted_values(self):
        d = DriftItem(field_path="image.tag", declared="v1.2.3", observed="latest", severity="critical")
        item = m._parse_drift_annotation("drift.image.tag", d.to_text())
        assert item["declared"] == "v1.2.3"
        assert item["observed"] == "latest"
        assert item["severity"] == "critical"

    def test_unparseable_returns_none(self):
        assert m._parse_drift_annotation("drift.x", "garbage") is None


# ─────────────────────────────────────────────────────────────────────────────
# _helm_drift — annotations live on workloads, reached via DRIFTS_FROM (reverse)
# ─────────────────────────────────────────────────────────────────────────────

def _graph_with_drift() -> OntologyGraph:
    g = OntologyGraph()
    rel = HelmRelease(uid="rel-1", name="api", namespace="prod")
    dep = Deployment(uid="dep-1", name="api", namespace="prod", replicas=3, ready_replicas=1)
    g.add_entity(rel)
    g.add_entity(dep)
    # Drift annotation as written by HelmDriftDetector (string form), on the workload
    drift = DriftItem(field_path="spec.replicas", declared=3, observed=1, severity="warning")
    dep.annotations["drift.spec.replicas"] = drift.to_text()
    # workload --DRIFTS_FROM--> release
    g.add_edge(Edge("dep-1", "rel-1", RelationshipType.DRIFTS_FROM))
    return g


def _patch_collectors(graph: OntologyGraph, drift_count: int = 1):
    """Patch the three ingestion classes so _helm_drift runs against `graph`."""
    k8s = MagicMock()
    k8s.return_value.collect.return_value = graph
    helm = MagicMock()
    helm.return_value.collect.return_value = None
    detector = MagicMock()
    detector.return_value.detect_all.return_value = drift_count
    return patch.multiple(
        "ingestion",
        K8sCollector=k8s,
        HelmCollector=helm,
        HelmDriftDetector=detector,
    )


class TestHelmDrift:
    def test_collects_drift_from_workload_via_reverse_edge(self):
        g = _graph_with_drift()
        with _patch_collectors(g, drift_count=1):
            res = _run(m._helm_drift({"release": "api", "namespace": "prod"}))
        assert res["release"] == "api"
        assert res["drift_count"] == 1
        assert len(res["drift_items"]) == 1
        item = res["drift_items"][0]
        assert item["field"] == "spec.replicas"
        assert item["declared"] == "3"
        assert item["observed"] == "1"
        assert item["resource"] == "Deployment/api"

    def test_release_name_filter(self):
        g = _graph_with_drift()
        with _patch_collectors(g, drift_count=1):
            res = _run(m._helm_drift({"release": "other", "namespace": "prod"}))
        assert res["drift_items"] == []

    def test_no_drift_yields_empty_items(self):
        g = OntologyGraph()
        g.add_entity(HelmRelease(uid="rel-1", name="api", namespace="prod"))
        with _patch_collectors(g, drift_count=0):
            res = _run(m._helm_drift({"release": "api", "namespace": "prod"}))
        assert res["drift_count"] == 0
        assert res["drift_items"] == []


# ─────────────────────────────────────────────────────────────────────────────
# _blast_radius — argument mapping / defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestBlastRadius:
    def test_passes_args_through(self):
        with patch("remediation.blast_radius.compute_blast_radius", return_value={"risk": "HIGH"}) as cbr:
            res = _run(m._blast_radius({
                "remediation_commands": ["kubectl delete pod api-0 -n prod"],
                "affected_resources": ["Pod/api-0"],
                "rollback_commands": ["kubectl rollout undo deploy/api -n prod"],
            }))
        assert res == {"risk": "HIGH"}
        cbr.assert_called_once_with(
            ["kubectl delete pod api-0 -n prod"],
            ["Pod/api-0"],
            ["kubectl rollout undo deploy/api -n prod"],
        )

    def test_optional_lists_default_to_empty(self):
        with patch("remediation.blast_radius.compute_blast_radius", return_value={"risk": "LOW"}) as cbr:
            _run(m._blast_radius({"remediation_commands": ["kubectl scale ..."]}))
        cbr.assert_called_once_with(["kubectl scale ..."], [], [])


# ─────────────────────────────────────────────────────────────────────────────
# dispatch_openai_tool_call — OpenAI function-calling adapter
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenAIAdapter:
    def _call(self, name, arguments, call_id="call_1"):
        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }

    def test_routes_to_handler_with_json_string_args(self):
        # OpenAI sends arguments as a JSON *string*.
        with patch.object(m, "_blast_radius", return_value={"risk": "HIGH"}):
            msg = _run(m.dispatch_openai_tool_call(
                self._call("blast_radius", '{"remediation_commands": ["kubectl delete pod x"]}')
            ))
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_1"
        assert msg["name"] == "blast_radius"
        assert json.loads(msg["content"]) == {"risk": "HIGH"}

    def test_accepts_already_parsed_dict_args(self):
        with patch.object(m, "_blast_radius", return_value={"risk": "LOW"}):
            msg = _run(m.dispatch_openai_tool_call(
                self._call("blast_radius", {"remediation_commands": []})
            ))
        assert json.loads(msg["content"]) == {"risk": "LOW"}

    def test_unknown_tool_returns_error_content(self):
        msg = _run(m.dispatch_openai_tool_call(self._call("nope", "{}")))
        assert "Unknown tool" in json.loads(msg["content"])["error"]

    def test_invalid_json_arguments_return_error(self):
        msg = _run(m.dispatch_openai_tool_call(self._call("blast_radius", "{not json")))
        assert "invalid arguments" in json.loads(msg["content"])["error"]

    def test_handler_exception_returns_error_content(self):
        with patch.object(m, "_blast_radius", side_effect=RuntimeError("boom")):
            msg = _run(m.dispatch_openai_tool_call(self._call("blast_radius", "{}")))
        assert json.loads(msg["content"])["error"] == "boom"

    def test_adapter_uses_same_registry_as_mcp(self):
        # The schema file, the MCP server and the adapter must agree on tool names.
        assert set(m._HANDLERS) == {t.name for t in m._TOOLS}
