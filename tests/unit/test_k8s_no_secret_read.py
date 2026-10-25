"""kube-verdict reads no Secret — docs/diagnostics.md § Step 4 (decision B, 2026-10-13)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from ingestion.k8s_collector import K8sCollector
from ontology.discovery import _TYPED_KINDS

ROOT = Path(__file__).resolve().parents[2]


def _collector() -> K8sCollector:
    """A collector whose API clients are mocks returning empty lists."""
    c = K8sCollector.__new__(K8sCollector)
    c.kube_version = None
    empty = MagicMock()
    empty.items = []
    for name in ("_core", "_apps", "_net"):
        setattr(c, name, MagicMock())
    c._core.list_namespace.return_value = empty
    c._ingress_lister = lambda graph, ns: None
    c._discovery = MagicMock()
    c._discovery.discover.return_value = []
    return c


def test_collect_never_lists_secrets():
    c = _collector()
    for attr in ("list_namespaced_pod", "list_namespaced_service", "list_namespaced_config_map",
                 "list_namespaced_persistent_volume_claim", "list_namespaced_service_account",
                 "list_namespaced_event", "list_persistent_volume", "list_node"):
        getattr(c._core, attr).return_value.items = []
    c.collect(namespaces=["production"])
    # the per-namespace loop really ran: ConfigMaps (listed just before Secrets used to be)
    c._core.list_namespaced_config_map.assert_called_with("production")
    c._core.list_namespaced_secret.assert_not_called()
    c._core.read_namespaced_secret.assert_not_called()
    c._core.list_secret_for_all_namespaces.assert_not_called()


def test_discovery_keeps_skipping_secrets():
    assert "Secret" in _TYPED_KINDS


def test_rbac_manifests_grant_no_secrets():
    raw = yaml.safe_load_all((ROOT / "k8s/rbac.yaml").read_text())
    for doc in raw:
        for rule in (doc or {}).get("rules", []):
            assert "secrets" not in rule.get("resources", []), rule
    helm = (ROOT / "helm/kube-verdict/templates/rbac.yaml").read_text()
    # the one opt-in exception (docs/diagnostics.md, amended 2026-10-23; fix plan H45): Secrets
    # only in the Role rendered per listed namespace, none by default (test_chart_rbac.py)
    before, opt_in, _ = helm.partition("{{- range .Values.rbac.helmReleaseNamespaces }}")
    assert opt_in and "secrets" not in before
