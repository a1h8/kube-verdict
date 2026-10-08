"""K8sCollector picks the Ingress API from the server version and the installed client.

kubernetes 37.0.0 removed NetworkingV1beta1Api; on a pre-1.19 cluster the
collector must then skip ingresses instead of failing at construction.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from ingestion import k8s_collector
from ingestion.k8s_collector import K8sCollector


def _build(supports_v1_ingress: bool) -> K8sCollector:
    fake_version = MagicMock()
    fake_version.changelog_notes.return_value = []
    fake_version.supports_networking_v1_ingress = supports_v1_ingress

    with patch.object(k8s_collector.k8s_config, "load_kube_config"), \
         patch.object(k8s_collector.k8s_config, "load_incluster_config"), \
         patch.object(k8s_collector.k8s_client, "ApiClient"), \
         patch.object(k8s_collector.k8s_client, "CoreV1Api"), \
         patch.object(k8s_collector.k8s_client, "AppsV1Api"), \
         patch.object(k8s_collector.k8s_client, "NetworkingV1Api"), \
         patch.object(k8s_collector, "detect_version", return_value=fake_version):
        return K8sCollector(kubeconfig="/tmp/kc")


def test_v1_cluster_uses_networking_v1():
    collector = _build(supports_v1_ingress=True)
    assert collector._ingress_lister == collector._list_ingress_v1


def test_old_cluster_uses_v1beta1_when_client_has_it(monkeypatch):
    monkeypatch.setattr(k8s_collector.k8s_client, "NetworkingV1beta1Api", MagicMock(), raising=False)
    collector = _build(supports_v1_ingress=False)
    assert collector._ingress_lister == collector._list_ingress_v1beta1


def test_old_cluster_skips_ingresses_when_client_lacks_v1beta1(monkeypatch, caplog):
    monkeypatch.delattr(k8s_collector.k8s_client, "NetworkingV1beta1Api", raising=False)
    with caplog.at_level(logging.WARNING, logger=k8s_collector.log.name):
        collector = _build(supports_v1_ingress=False)

    assert collector._ingress_api is None
    assert "v1beta1" in caplog.text
    graph = MagicMock()
    collector._collect_ingresses(graph, "default")
    graph.add_entity.assert_not_called()
