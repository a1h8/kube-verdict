"""A second investigation on the same thread collects the cluster again —
docs/fix-plan-001.md, H9 (the API reuses the session id as thread id)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import workflow.nodes as nodes
from ontology.graph import OntologyGraph
from workflow.nodes import _get_infra, ingest_node


def _ingest(graph, thread_id="session-1"):
    k8s = MagicMock(); k8s.collect.return_value = graph; k8s.kube_version = None
    policy = MagicMock(last_error=None)
    policy.collect.return_value = MagicMock(fail_count=0, audit_count=0, mutation_webhooks=0)
    with patch("ingestion.K8sCollector", return_value=k8s), \
            patch("ingestion.HelmCollector", return_value=MagicMock()), \
            patch("ingestion.HelmDriftDetector", return_value=MagicMock(detect_all=MagicMock(return_value=0))), \
            patch("ingestion.PolicyCollector", return_value=policy):
        out = ingest_node({"namespaces": ["prod"]}, {"configurable": {"thread_id": thread_id}})
    return out, k8s


def test_the_second_investigation_collects_again():
    before, after = OntologyGraph(), OntologyGraph()
    out1, k8s1 = _ingest(before)
    out2, k8s2 = _ingest(after)
    k8s2.collect.assert_called_once()                        # not "pre-built graph provided"
    cfg = {"configurable": {"thread_id": "session-1"}}
    assert _get_infra(cfg, out2)[0] is after
    assert _get_infra(cfg, out1)[0] is before                 # control: a resumed run keeps its own


def test_the_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(nodes, "_INFRA_CACHE_MAX", 3)
    for i in range(6):
        _ingest(OntologyGraph(), thread_id=f"t{i}")
    assert len(nodes._INFRA_CACHE) <= 3
