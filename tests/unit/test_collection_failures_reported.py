"""A collector that could not read reports it; its empty result is not read as absence —
docs/fix-plan-001.md, H5 / H24."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from kubernetes.client import ApiException

from ingestion.git_provider import LocalGitProvider
from ingestion.helm_collector import HelmCollector
from ontology.graph import OntologyGraph
from workflow.nodes import _ingestion_failures, gitops_node, ingest_node


def test_a_refused_pod_listing_is_a_listing_failure():
    from ingestion.k8s_collector import K8sCollector
    c = K8sCollector.__new__(K8sCollector)
    c._listing_failed = set()
    c._api_pods = {}
    c._core = MagicMock()
    c._core.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    c._collect_pods(OntologyGraph(), "prod")
    assert c.listing_failures == ["pods in prod"]


def _ingest(listing_failures=(), helm_failures=()):
    k8s = MagicMock()
    k8s.collect.return_value = OntologyGraph()
    k8s.kube_version = None
    k8s.listing_failures = list(listing_failures)
    helm = MagicMock()
    helm.failures = list(helm_failures)
    pol = MagicMock(last_error=None)
    pol.collect.return_value = MagicMock(fail_count=0, audit_count=0, mutation_webhooks=0)
    with patch("ingestion.K8sCollector", return_value=k8s), \
            patch("ingestion.HelmCollector", return_value=helm), \
            patch("ingestion.HelmDriftDetector", return_value=MagicMock(detect_all=MagicMock(return_value=0))), \
            patch("ingestion.PolicyCollector", return_value=pol):
        return ingest_node({"namespaces": ["prod"]}, {"configurable": {"thread_id": "f"}})


def test_ingest_reports_refused_listings_and_helm_failures():
    out = _ingest(["pods in prod"], ["helm get values api: Error: forbidden"])
    assert sorted(_ingestion_failures(out)) == ["helm", "ingest"]
    assert "pods in prod" in out["ingestion_stats"]["ingest"]["error"]


def test_a_clean_collection_reports_nothing():                            # control
    assert _ingestion_failures(_ingest()) == []


def test_helm_records_a_failed_call():
    h = HelmCollector()
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "helm", stderr="forbidden")):
        assert h._get_values("api", "prod") == {}
    assert h.failures and "forbidden" in h.failures[0]


def test_an_unreadable_repository_is_a_gitops_failure(tmp_path, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "GITOPS_ENABLED", True)
    monkeypatch.setattr(cfg, "GIT_REPOS", [{"url": "https://git.invalid/acme/infra.git"}])
    provider = LocalGitProvider("https://git.invalid/acme/infra.git", clone_dir=tmp_path)
    from ontology.entities import HelmRelease
    g = OntologyGraph()
    g.add_entity(HelmRelease(uid="r", name="api", namespace="prod", chart="api"))
    with patch("ingestion.git_provider.make_provider", return_value=provider):
        out = gitops_node({}, {"configurable": {"graph": g, "thread_id": "g"}})
    stat = out["ingestion_stats"]["gitops"]
    assert stat["fallback"] is True and "clone failed" in stat["error"]


def test_a_refused_crd_listing_is_recorded_by_discovery():
    from ontology.discovery import APIResourceInfo, APIServerDiscovery
    d = APIServerDiscovery.__new__(APIServerDiscovery)
    d.failures = []
    d._client = MagicMock()
    d._client.call_api.side_effect = ApiException(status=403, reason="Forbidden")
    res = APIResourceInfo(group="cert-manager.io", version="v1", kind="Certificate",
                          plural="certificates", namespaced=True, verbs=["list"])
    assert list(d._call_list("/apis/cert-manager.io/v1/certificates", res)) == []
    assert d.failures == ["Certificate at /apis/cert-manager.io/v1/certificates: 403"]


def test_discovery_refusals_are_their_own_failure():
    k8s = MagicMock()
    k8s.collect.return_value = OntologyGraph()
    k8s.kube_version = None
    k8s.listing_failures = []
    k8s._discovery.failures = ["Certificate at /apis/x: 403"]
    helm = MagicMock()
    helm.failures = []
    pol = MagicMock(last_error=None)
    pol.collect.return_value = MagicMock(fail_count=0, audit_count=0, mutation_webhooks=0)
    with patch("ingestion.K8sCollector", return_value=k8s), \
            patch("ingestion.HelmCollector", return_value=helm), \
            patch("ingestion.HelmDriftDetector", return_value=MagicMock(detect_all=MagicMock(return_value=0))), \
            patch("ingestion.PolicyCollector", return_value=pol):
        out = ingest_node({"namespaces": ["prod"]}, {"configurable": {"thread_id": "d"}})
    assert _ingestion_failures(out) == ["discovery"]
