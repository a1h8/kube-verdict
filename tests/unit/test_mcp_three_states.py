"""The MCP drift tools use the workflow's comparisons — docs/fix-plan-001.md H17,
docs/three-states.md: Git's render is compared with what Helm applied, never with the live
objects behind the operator's back; without an applied state the result says so."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from mcp_server import _rendered_expected_drift
from ontology.entities import Deployment
from ontology.graph import OntologyGraph


def _manifest(memory):
    return {"kind": "Deployment", "metadata": {"name": "api", "namespace": "prod"},
            "spec": {"replicas": 1, "template": {"spec": {"containers": [
                {"name": "api", "image": "api:1", "resources": {"limits": {"memory": memory}}}]}}}}


def _graph(live_memory):
    g = OntologyGraph()
    g.add_entity(Deployment(uid="d", name="api", namespace="prod", replicas=1,
                            containers=[{"name": "api", "image": "api:1",
                                         "resources": {"limits": {"memory": live_memory}}}]))
    return g


def _run(graph, applied):
    store = MagicMock(); store.versions.return_value = ["1.0.0"]
    store.get.return_value = MagicMock(name="api", version="1.0.0", render_type="helm")
    with patch("knowledge.chart_indexer.ChartIndexer.render", return_value=[_manifest("512Mi")]):
        _, items = _rendered_expected_drift(graph, "api", None, "prod", store, applied)
    return {(i["field"], i["provenance"]) for i in items}


def test_with_the_applied_manifest_git_is_compared_with_it():
    # Git 512Mi, applied 1Gi, live 128Mi (also edited): A sees 512Mi vs 1Gi, not vs 128Mi
    items = _run(_graph("128Mi"), [_manifest("1Gi")])
    assert items == {("container.api.resources.limits.memory", "git-vs-deployed")}


def test_without_it_the_comparison_is_labelled_declared_vs_live():                 # control
    items = _run(_graph("128Mi"), None)
    assert items == {("container.api.resources.limits.memory", "declared-vs-live")}


def test_git_equal_to_applied_is_no_drift_whatever_the_live():
    assert _run(_graph("128Mi"), [_manifest("512Mi")]) == set()
