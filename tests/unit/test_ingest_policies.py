"""The workflow collects policy reports on every run — docs/fix-plan-001.md, H2."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from kubernetes.client import ApiException

from ingestion.policy_collector import PolicyCollector
from ontology.graph import OntologyGraph
from workflow.nodes import _ingestion_failures, ingest_node


def _run_ingest(policy_collector, thread_id):
    graph = OntologyGraph()
    k8s = MagicMock(); k8s.collect.return_value = graph; k8s.kube_version = None
    with patch("ingestion.K8sCollector", return_value=k8s), \
            patch("ingestion.HelmCollector", return_value=MagicMock()), \
            patch("ingestion.HelmDriftDetector", return_value=MagicMock(detect_all=MagicMock(return_value=0))), \
            patch("ingestion.PolicyCollector", return_value=policy_collector) as cls:
        out = ingest_node({"namespaces": ["prod"]}, {"configurable": {"thread_id": thread_id}})
    return out, cls, k8s


def test_ingest_collects_policy_reports_with_the_collectors_client():
    collector = MagicMock(last_error=None)
    collector.collect.return_value = MagicMock(fail_count=2, audit_count=1, mutation_webhooks=3)
    out, cls, k8s = _run_ingest(collector, "policy-ok")
    collector.collect.assert_called_once()
    assert cls.call_args.kwargs["api_client"] is k8s.api_client       # impersonation applies
    assert out["ingestion_stats"]["policy"] == {"fail": 2, "audit": 1, "webhooks": 3, "fallback": False}
    assert _ingestion_failures(out) == []                                # control


def test_a_policy_failure_is_reported_not_swallowed():
    collector = MagicMock()
    collector.collect.side_effect = RuntimeError("boom")
    out, _, _ = _run_ingest(collector, "policy-fail")
    stat = out["ingestion_stats"]["policy"]
    assert stat["fallback"] is True and "boom" in stat["error"]
    assert _ingestion_failures(out) == ["policy"]
    assert "error" not in out          # the investigation goes on


def test_a_refused_listing_is_a_failure_not_no_violation():
    collector = MagicMock(last_error="PolicyReports: 403 Forbidden")
    collector.collect.return_value = MagicMock(fail_count=0, audit_count=0, mutation_webhooks=0)
    out, _, _ = _run_ingest(collector, "policy-403")
    assert out["ingestion_stats"]["policy"]["fallback"] is True
    assert _ingestion_failures(out) == ["policy"]


def _collector(custom_error, admission_ok=True):
    pc = PolicyCollector.__new__(PolicyCollector)
    pc._timeout = 1
    pc.last_error = None
    pc._custom = MagicMock()
    pc._custom.list_namespaced_custom_object.side_effect = custom_error
    pc._custom.list_cluster_custom_object.side_effect = custom_error
    pc._admission = MagicMock()
    pc._admission.list_mutating_webhook_configuration.return_value = MagicMock(items=[])
    return pc


def test_policy_collector_exposes_a_403():
    pc = _collector(ApiException(status=403, reason="Forbidden"))
    pc.collect(OntologyGraph(), namespaces=["prod"])
    assert pc.last_error and "403" in pc.last_error


def test_a_missing_crd_is_not_an_error():
    pc = _collector(ApiException(status=404, reason="Not Found"))
    pc.collect(OntologyGraph(), namespaces=["prod"])
    assert pc.last_error is None
