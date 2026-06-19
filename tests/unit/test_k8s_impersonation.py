"""RBAC-aware scoping — K8sCollector sets apiserver impersonation headers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import config as cfg
from ingestion import k8s_collector
from ingestion.k8s_collector import K8sCollector


def _build(**kwargs):
    """Construct a K8sCollector with all live-cluster init mocked out, returning
    the headers set on its ApiClient."""
    headers: dict[str, list[str]] = {}

    class FakeApiClient:
        def set_default_header(self, k, v):
            headers.setdefault(k, []).append(v)

    fake_version = MagicMock()
    fake_version.changelog_notes.return_value = []
    fake_version.supports_networking_v1_ingress = False

    with patch.object(k8s_collector.k8s_config, "load_kube_config"), \
         patch.object(k8s_collector.k8s_config, "load_incluster_config"), \
         patch.object(k8s_collector.k8s_client, "ApiClient", FakeApiClient), \
         patch.object(k8s_collector.k8s_client, "CoreV1Api"), \
         patch.object(k8s_collector.k8s_client, "AppsV1Api"), \
         patch.object(k8s_collector.k8s_client, "NetworkingV1Api"), \
         patch.object(k8s_collector, "detect_version", return_value=fake_version):
        K8sCollector(kubeconfig="/tmp/kc", **kwargs)
    return headers


def test_no_impersonation_by_default(monkeypatch):
    monkeypatch.setattr(cfg, "KUBE_IMPERSONATE_USER", None)
    monkeypatch.setattr(cfg, "KUBE_IMPERSONATE_GROUPS", [])
    headers = _build()
    assert "Impersonate-User" not in headers


def test_impersonate_user_and_groups_via_args(monkeypatch):
    monkeypatch.setattr(cfg, "KUBE_IMPERSONATE_USER", None)
    monkeypatch.setattr(cfg, "KUBE_IMPERSONATE_GROUPS", [])
    headers = _build(impersonate_user="tenant-a", impersonate_groups=["dev", "viewers"])
    assert headers["Impersonate-User"] == ["tenant-a"]
    assert headers["Impersonate-Group"] == ["dev", "viewers"]


def test_impersonation_from_config(monkeypatch):
    monkeypatch.setattr(cfg, "KUBE_IMPERSONATE_USER", "svc-tenant-b")
    monkeypatch.setattr(cfg, "KUBE_IMPERSONATE_GROUPS", ["team-b"])
    headers = _build()
    assert headers["Impersonate-User"] == ["svc-tenant-b"]
    assert headers["Impersonate-Group"] == ["team-b"]
