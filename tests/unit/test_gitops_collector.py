"""
Unit tests for the GitOps collector pipeline:
  GitProvider (Local + GitHub), ManifestRenderer, ManifestDiffer, GitopsCollector.

All tests are offline — no real git clone, no real helm, no cluster.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from ingestion.git_provider import GithubProvider, LocalGitProvider
from ingestion.manifest_differ import ManifestDiffer, _find_entity
from ingestion.manifest_renderer import ManifestRenderer
from ingestion.gitops_collector import GitopsCollector
from ontology.entities import (
    Deployment, HelmRelease, Pod, Service, ResourceKind,
)
from ontology.graph import OntologyGraph


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def simple_graph():
    g = OntologyGraph()
    g.add_entity(Deployment(
        uid="deploy-api", name="api", namespace="production",
        replicas=3, ready_replicas=1,
    ))
    g.add_entity(HelmRelease(
        uid="helm-production-api", name="api", namespace="production",
        chart="myrepo/api", chart_version="1.2.3",
        values={"replicaCount": 3},
        source="helm",
    ))
    g.add_entity(Service(
        uid="svc-api", name="api-svc", namespace="production",
        ports=[{"port": 80, "protocol": "TCP"}],
    ))
    return g


def _rendered_deployment(replicas: int = 3, image: str = "myapp:1.2.3") -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "api", "namespace": "production"},
        "spec": {
            "replicas": replicas,
            "template": {
                "spec": {
                    "containers": [
                        {"name": "api", "image": image,
                         "resources": {"requests": {"cpu": "100m"}}}
                    ]
                }
            }
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# ManifestRenderer
# ─────────────────────────────────────────────────────────────────────────────

class TestManifestRenderer:
    def test_parse_multidoc(self):
        text = "---\nkind: Deployment\napiVersion: apps/v1\n---\nkind: Service\n"
        docs = ManifestRenderer._parse_multidoc(text)
        assert len(docs) == 2
        assert docs[0]["kind"] == "Deployment"
        assert docs[1]["kind"] == "Service"

    def test_parse_multidoc_skips_empty(self):
        text = "---\n---\nkind: ConfigMap\n"
        docs = ManifestRenderer._parse_multidoc(text)
        assert len(docs) == 1

    def test_flatten_simple(self):
        result = ManifestRenderer._flatten({"a": 1, "b": "x"})
        assert result == {"a": "1", "b": "x"}

    def test_flatten_nested(self):
        result = ManifestRenderer._flatten({"image": {"tag": "1.0", "repo": "reg"}})
        assert result["image.tag"] == "1.0"
        assert result["image.repo"] == "reg"

    def test_flatten_skips_lists(self):
        result = ManifestRenderer._flatten({"ports": [80, 443]})
        assert "ports" not in result

    def test_helm_failure_returns_empty(self):
        renderer = ManifestRenderer()
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "helm")):
            result = renderer.render("chart", "release", "ns")
        assert result == []

    def test_helm_timeout_returns_empty(self):
        renderer = ManifestRenderer()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("helm", 60)):
            result = renderer.render("chart", "release", "ns")
        assert result == []

    def test_render_passes_correct_args(self):
        renderer = ManifestRenderer()
        mock_result = MagicMock()
        mock_result.stdout = "kind: Deployment\nmetadata:\n  name: api\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            renderer.render(
                chart="myrepo/api",
                release_name="api",
                namespace="production",
                values={"replicaCount": 3},
                chart_version="1.2.3",
            )
        cmd = mock_run.call_args[0][0]
        assert "helm" in cmd
        assert "template" in cmd
        assert "api" in cmd
        assert "myrepo/api" in cmd
        assert "--version" in cmd
        assert "--set" in cmd

    def test_render_with_value_files(self):
        renderer = ManifestRenderer()
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            renderer.render("chart", "rel", "ns", value_files=["values.yaml"])
        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        assert "values.yaml" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# ManifestDiffer
# ─────────────────────────────────────────────────────────────────────────────

class TestManifestDiffer:
    def test_no_drift_when_replicas_match(self, simple_graph):
        differ = ManifestDiffer()
        rendered = [_rendered_deployment(replicas=3)]
        drifts = differ.diff(rendered, simple_graph)
        replica_drifts = [d for d in drifts if "replicas" in d.field_path]
        assert replica_drifts == []

    def test_replica_drift_detected(self, simple_graph):
        differ = ManifestDiffer()
        rendered = [_rendered_deployment(replicas=5)]
        drifts = differ.diff(rendered, simple_graph)
        replica_drifts = [d for d in drifts if "replicas" in d.field_path]
        assert len(replica_drifts) == 1
        assert replica_drifts[0].declared == 5
        assert replica_drifts[0].observed == 3
        assert replica_drifts[0].source == "gitops"

    def test_replica_drift_critical_when_large_delta(self, simple_graph):
        differ = ManifestDiffer()
        rendered = [_rendered_deployment(replicas=10)]
        drifts = differ.diff(rendered, simple_graph)
        replica_drifts = [d for d in drifts if "replicas" in d.field_path]
        assert replica_drifts[0].severity == "critical"

    def test_replica_drift_warning_when_small_delta(self, simple_graph):
        differ = ManifestDiffer()
        rendered = [_rendered_deployment(replicas=4)]
        drifts = differ.diff(rendered, simple_graph)
        replica_drifts = [d for d in drifts if "replicas" in d.field_path]
        assert replica_drifts[0].severity == "warning"

    def test_missing_resource_detected(self, simple_graph):
        differ = ManifestDiffer()
        rendered = [{
            "kind": "Deployment",
            "metadata": {"name": "worker", "namespace": "production"},
            "spec": {"replicas": 2, "template": {"spec": {"containers": []}}},
        }]
        drifts = differ.diff(rendered, simple_graph)
        missing = [d for d in drifts if d.observed == "missing"]
        assert len(missing) == 1
        assert missing[0].severity == "critical"

    def test_untracked_kind_not_flagged_as_missing(self, simple_graph):
        differ = ManifestDiffer()
        rendered = [{
            "kind": "SomeCustomCRD",
            "metadata": {"name": "myobj", "namespace": "production"},
            "spec": {},
        }]
        drifts = differ.diff(rendered, simple_graph)
        missing = [d for d in drifts if d.observed == "missing"]
        assert missing == []

    def test_drift_annotated_on_entity(self, simple_graph):
        differ = ManifestDiffer()
        rendered = [_rendered_deployment(replicas=10)]
        differ.diff(rendered, simple_graph)
        deploy = simple_graph.get("deploy-api")
        assert any(k.startswith("gitops.") for k in deploy.annotations)

    def test_no_rendered_manifests_no_drifts(self, simple_graph):
        differ = ManifestDiffer()
        drifts = differ.diff([], simple_graph)
        assert drifts == []

    def test_find_entity_by_kind_name_namespace(self, simple_graph):
        entity = _find_entity(simple_graph, "Deployment", "api", "production")
        assert entity is not None
        assert entity.uid == "deploy-api"

    def test_find_entity_returns_none_for_unknown(self, simple_graph):
        entity = _find_entity(simple_graph, "Deployment", "nonexistent", "production")
        assert entity is None

    def test_find_entity_namespace_mismatch(self, simple_graph):
        entity = _find_entity(simple_graph, "Deployment", "api", "staging")
        assert entity is None


# ─────────────────────────────────────────────────────────────────────────────
# GitopsCollector
# ─────────────────────────────────────────────────────────────────────────────

class TestGitopsCollector:
    def _make_collector(self, rendered: list[dict]) -> GitopsCollector:
        provider = MagicMock()
        provider.local_path.return_value = None

        renderer = MagicMock()
        renderer.render.return_value = rendered

        return GitopsCollector(provider=provider, renderer=renderer)

    def test_collects_drifts_for_release(self, simple_graph):
        collector = self._make_collector([_rendered_deployment(replicas=10)])
        drifts = collector.collect(simple_graph)
        assert len(drifts) > 0

    def test_annotates_release_drift_count(self, simple_graph):
        collector = self._make_collector([_rendered_deployment(replicas=10)])
        collector.collect(simple_graph)
        release = simple_graph.get("helm-production-api")
        assert "gitops.drift_count" in release.annotations

    def test_empty_rendered_no_drifts(self, simple_graph):
        collector = self._make_collector([])
        drifts = collector.collect(simple_graph)
        assert drifts == []

    def test_skips_when_chart_unresolvable(self):
        g = OntologyGraph()
        g.add_entity(HelmRelease(
            uid="helm-rel", name="noref", namespace="default",
            chart="", chart_version="",
        ))
        provider = MagicMock()
        provider.local_path.return_value = None
        renderer = MagicMock()
        collector = GitopsCollector(provider=provider, renderer=renderer)
        drifts = collector.collect(g)
        renderer.render.assert_not_called()
        assert drifts == []

    def test_resolve_local_chart(self, tmp_path):
        chart_dir = tmp_path / "charts" / "api"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("name: api\nversion: 1.0.0\n")

        provider = MagicMock()
        provider.local_path.return_value = tmp_path
        renderer = MagicMock()
        renderer.render.return_value = []
        collector = GitopsCollector(provider=provider, charts_path="charts",
                                    renderer=renderer)

        g = OntologyGraph()
        g.add_entity(HelmRelease(
            uid="helm-api", name="api", namespace="default",
            chart="api", chart_version="1.0.0",
        ))
        collector.collect(g)

        call_kwargs = renderer.render.call_args
        assert str(chart_dir) in call_kwargs[1].get("chart", "") or \
               str(chart_dir) in str(call_kwargs)

    def test_remote_chart_ref_passed_through(self, simple_graph):
        provider = MagicMock()
        provider.local_path.return_value = None
        renderer = MagicMock()
        renderer.render.return_value = []
        collector = GitopsCollector(provider=provider, renderer=renderer)
        collector.collect(simple_graph)

        call_kwargs = renderer.render.call_args
        assert call_kwargs is not None
        chart_arg = call_kwargs[1].get("chart") or call_kwargs[0][0]
        assert "myrepo/api" in str(chart_arg)


# ─────────────────────────────────────────────────────────────────────────────
# LocalGitProvider
# ─────────────────────────────────────────────────────────────────────────────

class TestLocalGitProvider:
    def test_get_file_reads_existing_file(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "values.yaml").write_text("replicaCount: 3\n")

        provider = LocalGitProvider.__new__(LocalGitProvider)
        provider.repo_url = "https://github.com/test/repo.git"
        provider.branch = "main"
        provider._base = tmp_path.parent

        with patch.object(provider, "_ensure_cloned", return_value=tmp_path):
            content = provider.get_file("values.yaml")
        assert content == "replicaCount: 3\n"

    def test_get_file_returns_none_on_missing(self, tmp_path):
        provider = LocalGitProvider.__new__(LocalGitProvider)
        with patch.object(provider, "_ensure_cloned", return_value=tmp_path):
            content = provider.get_file("nonexistent.yaml")
        assert content is None

    def test_list_files_returns_yaml_files(self, tmp_path):
        charts = tmp_path / "charts" / "api"
        charts.mkdir(parents=True)
        (charts / "Chart.yaml").write_text("name: api\n")
        (charts / "values.yaml").write_text("replicaCount: 1\n")
        (charts / "README.md").write_text("docs")

        provider = LocalGitProvider.__new__(LocalGitProvider)
        with patch.object(provider, "_ensure_cloned", return_value=tmp_path):
            files = provider.list_files("charts", "*.yaml")

        yaml_files = [f for f in files if f.endswith(".yaml")]
        assert len(yaml_files) == 2

    def test_local_path_returns_cloned_dir(self, tmp_path):
        provider = LocalGitProvider.__new__(LocalGitProvider)
        with patch.object(provider, "_ensure_cloned", return_value=tmp_path):
            assert provider.local_path() == tmp_path

    def test_clone_failure_returns_none_local_path(self):
        provider = LocalGitProvider.__new__(LocalGitProvider)
        with patch.object(provider, "_ensure_cloned",
                          side_effect=subprocess.CalledProcessError(128, "git")):
            assert provider.local_path() is None


# ─────────────────────────────────────────────────────────────────────────────
# GithubProvider
# ─────────────────────────────────────────────────────────────────────────────

class TestGithubProvider:
    def test_get_file_decodes_base64(self):
        import base64
        content = base64.b64encode(b"replicaCount: 3\n").decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"encoding": "base64", "content": content}
        mock_resp.raise_for_status = MagicMock()

        provider = GithubProvider("myorg/infra", ref="main")
        with patch("requests.get", return_value=mock_resp):
            result = provider.get_file("values.yaml")
        assert result == "replicaCount: 3\n"

    def test_get_file_returns_none_on_http_error(self):
        import requests as req
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError("404")

        provider = GithubProvider("myorg/infra")
        with patch("requests.get", return_value=mock_resp):
            result = provider.get_file("missing.yaml")
        assert result is None

    def test_list_files_filters_by_directory_and_suffix(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": [
            {"type": "blob", "path": "charts/api/Chart.yaml"},
            {"type": "blob", "path": "charts/api/values.yaml"},
            {"type": "blob", "path": "docs/README.md"},
            {"type": "tree", "path": "charts/api"},
        ]}
        mock_resp.raise_for_status = MagicMock()

        provider = GithubProvider("myorg/infra")
        with patch("requests.get", return_value=mock_resp):
            files = provider.list_files("charts", "*.yaml")
        assert "charts/api/Chart.yaml" in files
        assert "charts/api/values.yaml" in files
        assert "docs/README.md" not in files

    def test_local_path_is_none(self):
        provider = GithubProvider("myorg/infra")
        assert provider.local_path() is None
