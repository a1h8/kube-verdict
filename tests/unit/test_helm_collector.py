"""
Unit tests for HelmCollector — all subprocess calls mocked.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from ingestion.helm_collector import HelmCollector
from ingestion.chart_parser import ChartParser
from ontology.entities import (
    HelmRelease, HelmChart, HelmRepository, Pod,
)
from ontology.graph import OntologyGraph


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run_ok(stdout: str) -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = 0
    return r


def _run_fail(stderr: str = "helm error") -> subprocess.CalledProcessError:
    exc = subprocess.CalledProcessError(1, ["helm"])
    exc.stderr = stderr
    return exc


def _fake_release(name: str = "api", namespace: str = "prod", chart: str = "stable/api-1.2.3") -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "chart": chart,
        "app_version": "1.0.0",
        "status": "deployed",
        "updated": "2026-05-01T10:00:00Z",
    }


# ─────────────────────────────────────────────────────────────────────────────
# _parse_chart_name / _parse_chart_version
# ─────────────────────────────────────────────────────────────────────────────

class TestParseChart:
    def test_parse_name_normal(self):
        assert HelmCollector._parse_chart_name("nginx-1.2.3") == "nginx"

    def test_parse_name_no_version(self):
        assert HelmCollector._parse_chart_name("nginx") == "nginx"

    def test_parse_name_multi_hyphen(self):
        assert HelmCollector._parse_chart_name("my-chart-1.0.0") == "my-chart"

    def test_parse_version_normal(self):
        assert HelmCollector._parse_chart_version("nginx-1.2.3") == "1.2.3"

    def test_parse_version_no_version(self):
        assert HelmCollector._parse_chart_version("nginx") == ""

    def test_parse_version_semver(self):
        assert HelmCollector._parse_chart_version("my-chart-0.1.0") == "0.1.0"


# ─────────────────────────────────────────────────────────────────────────────
# _run_json
# ─────────────────────────────────────────────────────────────────────────────

class TestRunJson:
    def test_returns_parsed_json(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok('[{"name":"api"}]')):
            result = c._run_json(["helm", "list"], "helm list")
        assert result == [{"name": "api"}]

    def test_returns_empty_dict_on_failure(self):
        c = HelmCollector()
        with patch("subprocess.run", side_effect=_run_fail()):
            result = c._run_json(["helm", "list"], "helm list")
        assert result == {}

    def test_returns_empty_dict_on_invalid_json(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok("not-json")):
            result = c._run_json(["helm", "list"], "helm list")
        assert result == {}

    def test_empty_stdout_returns_empty_dict(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok("")):
            result = c._run_json(["helm", "list"], "helm list")
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# _get_notes
# ─────────────────────────────────────────────────────────────────────────────

class TestGetNotes:
    def test_returns_stripped_notes(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok("  some notes\n")):
            assert c._get_notes("api", "prod") == "some notes"

    def test_returns_empty_on_failure(self):
        c = HelmCollector()
        with patch("subprocess.run", side_effect=_run_fail()):
            assert c._get_notes("api", "prod") == ""

    def test_cmd_includes_release_and_namespace(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok("")) as mock_run:
            c._get_notes("myapp", "staging")
        cmd = mock_run.call_args[0][0]
        assert "myapp" in cmd
        assert "staging" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# _get_values
# ─────────────────────────────────────────────────────────────────────────────

class TestGetValues:
    def test_returns_dict(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok('{"replicas": 3}')):
            result = c._get_values("api", "prod")
        assert result == {"replicas": 3}

    def test_returns_empty_on_non_dict(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok('[1, 2, 3]')):
            result = c._get_values("api", "prod")
        assert result == {}

    def test_include_defaults_adds_all_flag(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok("{}")) as mock_run:
            c._get_values("api", "prod", include_defaults=True)
        cmd = mock_run.call_args[0][0]
        assert "--all" in cmd

    def test_no_include_defaults_omits_all_flag(self):
        c = HelmCollector()
        with patch("subprocess.run", return_value=_run_ok("{}")) as mock_run:
            c._get_values("api", "prod", include_defaults=False)
        cmd = mock_run.call_args[0][0]
        assert "--all" not in cmd


# ─────────────────────────────────────────────────────────────────────────────
# _helm_list
# ─────────────────────────────────────────────────────────────────────────────

class TestHelmList:
    def test_all_namespaces_flag(self):
        c = HelmCollector()
        with patch.object(c, "_run_json", return_value=[]) as mock:
            c._helm_list(all_namespaces=True)
        cmd = mock.call_args[0][0]
        assert "--all-namespaces" in cmd

    def test_specific_namespace_flag(self):
        c = HelmCollector()
        with patch.object(c, "_run_json", return_value=[]) as mock:
            c._helm_list(namespace="staging")
        cmd = mock.call_args[0][0]
        assert "--namespace" in cmd
        assert "staging" in cmd

    def test_no_namespace_no_flag(self):
        c = HelmCollector()
        with patch.object(c, "_run_json", return_value=[]) as mock:
            c._helm_list()
        cmd = mock.call_args[0][0]
        assert "--namespace" not in cmd
        assert "--all-namespaces" not in cmd


# ─────────────────────────────────────────────────────────────────────────────
# _list_releases
# ─────────────────────────────────────────────────────────────────────────────

class TestListReleases:
    def test_all_namespaces_when_none(self):
        c = HelmCollector()
        with patch.object(c, "_helm_list", return_value=[]) as mock:
            c._list_releases(None)
        mock.assert_called_once_with(all_namespaces=True)

    def test_per_namespace_when_list(self):
        c = HelmCollector()
        with patch.object(c, "_helm_list", return_value=[]) as mock:
            c._list_releases(["prod", "staging"])
        assert mock.call_count == 2

    def test_skips_unsafe_namespace(self):
        c = HelmCollector()
        with patch.object(c, "_helm_list", return_value=[]) as mock:
            c._list_releases(["valid-ns", "../bad"])
        mock.assert_called_once_with(namespace="valid-ns")

    def test_results_concatenated(self):
        rel1 = [_fake_release("api", "prod")]
        rel2 = [_fake_release("db", "staging")]
        c = HelmCollector()
        with patch.object(c, "_helm_list", side_effect=[rel1, rel2]):
            result = c._list_releases(["prod", "staging"])
        assert len(result) == 2


# ─────────────────────────────────────────────────────────────────────────────
# _index_repos
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexRepos:
    def test_creates_helm_repository_nodes(self):
        c = HelmCollector()
        repos = [
            {"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"},
            {"name": "stable",  "url": "https://charts.helm.sh/stable"},
        ]
        g = OntologyGraph()
        with patch.object(c, "_run_json", return_value=repos):
            uid_map = c._index_repos(g)
        assert g.get("helmrepo-bitnami") is not None
        assert g.get("helmrepo-stable") is not None
        assert uid_map["bitnami"] == "helmrepo-bitnami"

    def test_oci_repo_type(self):
        c = HelmCollector()
        repos = [{"name": "ghcr", "url": "oci://ghcr.io/myorg"}]
        g = OntologyGraph()
        with patch.object(c, "_run_json", return_value=repos):
            c._index_repos(g)
        assert g.get("helmrepo-ghcr").repo_type == "oci"

    def test_non_list_response_returns_empty(self):
        c = HelmCollector()
        g = OntologyGraph()
        with patch.object(c, "_run_json", return_value={}):
            uid_map = c._index_repos(g)
        assert uid_map == {}

    def test_repo_without_name_skipped(self):
        c = HelmCollector()
        repos = [{"url": "https://charts.example.com"}]
        g = OntologyGraph()
        with patch.object(c, "_run_json", return_value=repos):
            uid_map = c._index_repos(g)
        assert uid_map == {}

    def test_existing_repo_not_duplicated(self):
        c = HelmCollector()
        repos = [{"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"}]
        g = OntologyGraph()
        existing = HelmRepository(uid="helmrepo-bitnami", name="bitnami")
        g.add_entity(existing)
        with patch.object(c, "_run_json", return_value=repos):
            c._index_repos(g)
        # Must still be the same object (not replaced)
        assert g.get("helmrepo-bitnami") is existing


# ─────────────────────────────────────────────────────────────────────────────
# _link_managed_resources
# ─────────────────────────────────────────────────────────────────────────────

class TestLinkManagedResources:
    def test_links_entity_with_helm_annotation(self):
        g = OntologyGraph()
        release = HelmRelease(uid="helm-prod-api", name="api", namespace="prod", source="helm")
        pod = Pod(
            uid="pod-api-0", name="api-0", namespace="prod",
            annotations={"meta.helm.sh/release-name": "api"},
            labels={},
        )
        g.add_entity(release)
        g.add_entity(pod)

        c = HelmCollector()
        c._link_managed_resources(g, release, "prod")

        neighbour_uids = {e.uid for e in g.neighbors("pod-api-0")}
        assert "helm-prod-api" in neighbour_uids

    def test_links_entity_with_managed_by_label(self):
        g = OntologyGraph()
        release = HelmRelease(uid="helm-prod-api", name="api", namespace="prod", source="helm")
        pod = Pod(
            uid="pod-api-1", name="api-1", namespace="prod",
            labels={"app.kubernetes.io/managed-by": "Helm"},
            annotations={},
        )
        g.add_entity(release)
        g.add_entity(pod)

        c = HelmCollector()
        c._link_managed_resources(g, release, "prod")

        neighbour_uids = {e.uid for e in g.neighbors("pod-api-1")}
        assert "helm-prod-api" in neighbour_uids

    def test_skips_different_namespace(self):
        g = OntologyGraph()
        release = HelmRelease(uid="helm-prod-api", name="api", namespace="prod", source="helm")
        pod = Pod(
            uid="pod-other", name="other", namespace="staging",
            annotations={"meta.helm.sh/release-name": "api"},
            labels={},
        )
        g.add_entity(release)
        g.add_entity(pod)

        c = HelmCollector()
        c._link_managed_resources(g, release, "prod")

        neighbour_uids = {e.uid for e in g.neighbors("pod-other")}
        assert "helm-prod-api" not in neighbour_uids


# ─────────────────────────────────────────────────────────────────────────────
# _wire_chart_deps
# ─────────────────────────────────────────────────────────────────────────────

class TestWireChartDeps:
    def test_no_sub_charts_no_edges(self):
        g = OntologyGraph()
        chart = HelmChart(uid="chart-api-1.0.0", name="api", chart_version="1.0.0")
        g.add_entity(chart)
        HelmCollector._wire_chart_deps(g, chart)
        assert len(list(g.entities())) == 1

    def test_sub_charts_added_and_linked(self):
        g = OntologyGraph()
        sub = HelmChart(uid="chart-redis-6.0.0", name="redis", chart_version="6.0.0")
        chart = HelmChart(uid="chart-api-1.0.0", name="api", chart_version="1.0.0")
        chart._sub_charts = [sub]
        g.add_entity(chart)

        HelmCollector._wire_chart_deps(g, chart)

        assert g.get("chart-redis-6.0.0") is not None
        neighbour_uids = {e.uid for e in g.neighbors("chart-api-1.0.0")}
        assert "chart-redis-6.0.0" in neighbour_uids

    def test_nested_sub_charts_recursive(self):
        g = OntologyGraph()
        grandchild = HelmChart(uid="chart-gc-1.0.0", name="gc", chart_version="1.0.0")
        grandchild._sub_charts = []
        child = HelmChart(uid="chart-child-1.0.0", name="child", chart_version="1.0.0")
        child._sub_charts = [grandchild]
        root = HelmChart(uid="chart-root-1.0.0", name="root", chart_version="1.0.0")
        root._sub_charts = [child]
        g.add_entity(root)

        HelmCollector._wire_chart_deps(g, root)

        assert g.get("chart-gc-1.0.0") is not None


# ─────────────────────────────────────────────────────────────────────────────
# collect() — full integration with mocked subprocess
# ─────────────────────────────────────────────────────────────────────────────

class TestCollect:
    def _make_collector(self) -> HelmCollector:
        return HelmCollector()

    def test_collect_creates_helm_release_node(self):
        c = self._make_collector()
        releases = [_fake_release("api", "prod", "stable/api-1.2.3")]

        with patch.object(c, "_list_releases", return_value=releases), \
             patch.object(c, "_get_values", return_value={"replicas": 2}), \
             patch.object(c, "_get_notes", return_value=""), \
             patch.object(c, "_index_repos", return_value={}), \
             patch.object(ChartParser, "from_helm_show", return_value=None):
            g = OntologyGraph()
            c.collect(g)

        release = g.get("helm-prod-api")
        assert release is not None
        assert isinstance(release, HelmRelease)

    def test_collect_skips_release_with_unsafe_name(self):
        c = self._make_collector()
        releases = [
            {"name": "../bad", "namespace": "prod", "chart": "x-1.0", "status": "deployed"},
        ]

        with patch.object(c, "_list_releases", return_value=releases), \
             patch.object(c, "_index_repos", return_value={}):
            g = OntologyGraph()
            c.collect(g)

        assert list(g.entities()) == []

    def test_collect_skips_release_with_unsafe_namespace(self):
        c = self._make_collector()
        releases = [
            {"name": "api", "namespace": "BAD NS!", "chart": "x-1.0", "status": "deployed"},
        ]

        with patch.object(c, "_list_releases", return_value=releases), \
             patch.object(c, "_index_repos", return_value={}):
            g = OntologyGraph()
            c.collect(g)

        assert list(g.entities()) == []

    def test_collect_wires_hosted_by_when_repo_prefix(self):
        c = self._make_collector()
        releases = [_fake_release("api", "prod", "bitnami/api-1.2.3")]
        chart_entity = HelmChart(uid="chart-api-1.2.3", name="api", chart_version="1.2.3")

        with patch.object(c, "_list_releases", return_value=releases), \
             patch.object(c, "_get_values", return_value={}), \
             patch.object(c, "_get_notes", return_value=""), \
             patch.object(c, "_index_repos", return_value={"bitnami": "helmrepo-bitnami"}), \
             patch.object(ChartParser, "from_helm_show", return_value=chart_entity):
            g = OntologyGraph()
            repo = HelmRepository(uid="helmrepo-bitnami", name="bitnami")
            g.add_entity(repo)
            c.collect(g)

        # HelmRelease → chart (DEPLOYED_FROM) and chart → repo (HOSTED_BY)
        chart = g.get("chart-api-1.2.3")
        assert chart is not None
        chart_neighbours = {e.uid for e in g.neighbors("chart-api-1.2.3")}
        assert "helmrepo-bitnami" in chart_neighbours

    def test_collect_no_hosted_by_for_local_chart(self):
        c = self._make_collector()
        releases = [_fake_release("api", "prod", "api-1.2.3")]  # no "/" prefix
        chart_entity = HelmChart(uid="chart-api-1.2.3", name="api", chart_version="1.2.3")

        with patch.object(c, "_list_releases", return_value=releases), \
             patch.object(c, "_get_values", return_value={}), \
             patch.object(c, "_get_notes", return_value=""), \
             patch.object(c, "_index_repos", return_value={"stable": "helmrepo-stable"}), \
             patch.object(ChartParser, "from_helm_show", return_value=chart_entity):
            g = OntologyGraph()
            c.collect(g)

        chart_neighbours = {e.uid for e in g.neighbors("chart-api-1.2.3")}
        repo_neighbours = [uid for uid in chart_neighbours if uid.startswith("helmrepo-")]
        assert repo_neighbours == []

    def test_collect_with_notes_sets_annotation(self):
        c = self._make_collector()
        releases = [_fake_release("api", "prod", "api-1.0.0")]

        with patch.object(c, "_list_releases", return_value=releases), \
             patch.object(c, "_get_values", return_value={}), \
             patch.object(c, "_get_notes", return_value="Chart deployed OK"), \
             patch.object(c, "_index_repos", return_value={}), \
             patch.object(ChartParser, "from_helm_show", return_value=None):
            g = OntologyGraph()
            c.collect(g)

        release = g.get("helm-prod-api")
        assert release.annotations.get("helm.sh/notes") == "Chart deployed OK"

    def test_collect_links_managed_resources(self):
        c = self._make_collector()
        releases = [_fake_release("api", "prod", "api-1.0.0")]

        with patch.object(c, "_list_releases", return_value=releases), \
             patch.object(c, "_get_values", return_value={}), \
             patch.object(c, "_get_notes", return_value=""), \
             patch.object(c, "_index_repos", return_value={}), \
             patch.object(ChartParser, "from_helm_show", return_value=None):
            g = OntologyGraph()
            pod = Pod(
                uid="pod-api-0", name="api-0", namespace="prod",
                annotations={"meta.helm.sh/release-name": "api"},
                labels={},
            )
            g.add_entity(pod)
            c.collect(g)

        neighbour_uids = {e.uid for e in g.neighbors("pod-api-0")}
        assert "helm-prod-api" in neighbour_uids
