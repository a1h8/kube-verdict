"""
Unit tests for the Helm/Helmfile ontology extensions:
HelmRepository, HelmfileEnvironment, HOSTED_BY, DEPLOYS_IN.
"""
import yaml

from ontology.entities import (
    HelmRepository, HelmfileEnvironment, HelmChart, HelmRelease, ResourceKind,
)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType
from ingestion.helmfile_collector import HelmfileCollector


# ─────────────────────────────────────────────────────────────────────────────
# HelmRepository
# ─────────────────────────────────────────────────────────────────────────────

class TestHelmRepository:
    def test_kind(self):
        r = HelmRepository(uid="helmrepo-bitnami", name="bitnami",
                           url="https://charts.bitnami.com/bitnami")
        assert r.kind == ResourceKind.HELM_REPOSITORY

    def test_to_text_contains_name_and_url(self):
        r = HelmRepository(uid="helmrepo-bitnami", name="bitnami",
                           url="https://charts.bitnami.com/bitnami")
        text = r.to_text()
        assert "bitnami" in text
        assert "charts.bitnami.com" in text

    def test_to_text_oci_type(self):
        r = HelmRepository(uid="helmrepo-ghcr", name="ghcr",
                           url="oci://ghcr.io/myorg", repo_type="oci")
        assert "oci" in r.to_text()

    def test_to_text_http_type_omitted(self):
        r = HelmRepository(uid="helmrepo-stable", name="stable",
                           url="https://charts.helm.sh/stable", repo_type="http")
        # "http" is the default — no need to clutter the text
        assert "type=http" not in r.to_text()

    def test_namespace_is_none(self):
        r = HelmRepository(uid="helmrepo-bitnami", name="bitnami")
        assert r.namespace is None

    def test_indexable_in_graph(self):
        g = OntologyGraph()
        r = HelmRepository(uid="helmrepo-bitnami", name="bitnami",
                           url="https://charts.bitnami.com/bitnami")
        g.add_entity(r)
        assert g.get("helmrepo-bitnami") is r


# ─────────────────────────────────────────────────────────────────────────────
# HelmfileEnvironment
# ─────────────────────────────────────────────────────────────────────────────

class TestHelmfileEnvironment:
    def test_kind(self):
        e = HelmfileEnvironment(uid="helmfile-env-production", name="production")
        assert e.kind == ResourceKind.HELMFILE_ENV

    def test_to_text_contains_name(self):
        e = HelmfileEnvironment(uid="helmfile-env-production", name="production",
                                kube_context="k3s-prod")
        text = e.to_text()
        assert "production" in text
        assert "k3s-prod" in text

    def test_to_text_value_files(self):
        e = HelmfileEnvironment(
            uid="helmfile-env-staging", name="staging",
            value_files=["values/common.yaml", "values/staging.yaml"],
        )
        assert "common.yaml" in e.to_text()
        assert "staging.yaml" in e.to_text()

    def test_to_text_inline_values(self):
        e = HelmfileEnvironment(
            uid="helmfile-env-prod", name="prod",
            values={"replicas": 3, "debug": False},
        )
        assert "replicas=3" in e.to_text()

    def test_namespace_is_none(self):
        e = HelmfileEnvironment(uid="helmfile-env-dev", name="dev")
        assert e.namespace is None


# ─────────────────────────────────────────────────────────────────────────────
# HOSTED_BY relationship
# ─────────────────────────────────────────────────────────────────────────────

class TestHostedByRelationship:
    def _make_graph(self):
        g = OntologyGraph()
        repo = HelmRepository(uid="helmrepo-bitnami", name="bitnami",
                              url="https://charts.bitnami.com/bitnami")
        chart = HelmChart(uid="chart-postgresql-13.2.0", name="postgresql",
                          chart_version="13.2.0")
        g.add_entity(repo)
        g.add_entity(chart)
        g.add_edge(Edge("chart-postgresql-13.2.0", "helmrepo-bitnami",
                        RelationshipType.HOSTED_BY))
        return g

    def test_hosted_by_edge_exists(self):
        g = self._make_graph()
        neighbour_uids = {e.uid for e in g.neighbors("chart-postgresql-13.2.0")}
        assert "helmrepo-bitnami" in neighbour_uids

    def test_bfs_reaches_repo_from_chart(self):
        g = self._make_graph()
        visited = g.bfs("chart-postgresql-13.2.0", max_depth=1)
        uids = {e.uid for e in visited}
        assert "helmrepo-bitnami" in uids

    def test_relationship_type_string(self):
        assert RelationshipType.HOSTED_BY == "HOSTED_BY"


# ─────────────────────────────────────────────────────────────────────────────
# DEPLOYS_IN relationship
# ─────────────────────────────────────────────────────────────────────────────

class TestDeploysInRelationship:
    def _make_graph(self):
        g = OntologyGraph()
        env = HelmfileEnvironment(uid="helmfile-env-production", name="production")
        rel = HelmRelease(uid="helm-production-api", name="api",
                          namespace="production", source="helmfile",
                          environment="production")
        g.add_entity(env)
        g.add_entity(rel)
        g.add_edge(Edge("helm-production-api", "helmfile-env-production",
                        RelationshipType.DEPLOYS_IN))
        return g

    def test_deploys_in_edge_exists(self):
        g = self._make_graph()
        neighbour_uids = {e.uid for e in g.neighbors("helm-production-api")}
        assert "helmfile-env-production" in neighbour_uids

    def test_bfs_reverse_from_env_reaches_release(self):
        g = self._make_graph()
        # Edge is release→env (directed), so reverse traversal from env finds releases
        reverse_neighbours = {e.uid for e in g.neighbors("helmfile-env-production", reverse=True)}
        assert "helm-production-api" in reverse_neighbours

    def test_relationship_type_string(self):
        assert RelationshipType.DEPLOYS_IN == "DEPLOYS_IN"


# ─────────────────────────────────────────────────────────────────────────────
# HelmfileCollector — repository and environment indexing
# ─────────────────────────────────────────────────────────────────────────────

def _write_helmfile(tmp_path, content: dict):
    path = tmp_path / "helmfile.yaml"
    path.write_text(yaml.dump(content))
    return path


class TestHelmfileCollectorOntology:
    def test_repositories_create_nodes(self, tmp_path):
        content = {
            "repositories": [
                {"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"},
                {"name": "stable",  "url": "https://charts.helm.sh/stable"},
            ],
            "releases": [],
        }
        path = _write_helmfile(tmp_path, content)
        g = OntologyGraph()
        HelmfileCollector(helmfile_path=path).collect(g)

        assert g.get("helmrepo-bitnami") is not None
        assert g.get("helmrepo-stable") is not None

    def test_repository_to_text_has_url(self, tmp_path):
        content = {
            "repositories": [
                {"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"},
            ],
            "releases": [],
        }
        path = _write_helmfile(tmp_path, content)
        g = OntologyGraph()
        HelmfileCollector(helmfile_path=path).collect(g)

        repo = g.get("helmrepo-bitnami")
        assert "charts.bitnami.com" in repo.to_text()

    def test_oci_repo_type_detected(self, tmp_path):
        content = {
            "repositories": [
                {"name": "ghcr", "url": "oci://ghcr.io/myorg"},
            ],
            "releases": [],
        }
        path = _write_helmfile(tmp_path, content)
        g = OntologyGraph()
        HelmfileCollector(helmfile_path=path).collect(g)

        repo = g.get("helmrepo-ghcr")
        assert repo.repo_type == "oci"

    def test_environment_creates_node(self, tmp_path):
        content = {
            "environments": {
                "production": {
                    "values": ["values/prod.yaml"],
                    "kubeContext": "k3s-prod",
                }
            },
            "releases": [],
        }
        path = _write_helmfile(tmp_path, content)
        g = OntologyGraph()
        HelmfileCollector(helmfile_path=path, environment="production").collect(g)

        env = g.get("helmfile-env-production")
        assert env is not None
        assert env.kube_context == "k3s-prod"

    def test_environment_node_absent_for_missing_env(self, tmp_path):
        content = {
            "environments": {"staging": {}},
            "releases": [],
        }
        path = _write_helmfile(tmp_path, content)
        g = OntologyGraph()
        HelmfileCollector(helmfile_path=path, environment="production").collect(g)

        assert g.get("helmfile-env-production") is None

    def test_deploys_in_edge_wired(self, tmp_path):
        content = {
            "environments": {"production": {}},
            "releases": [
                {"name": "api", "namespace": "default", "chart": "myrepo/api"},
            ],
        }
        path = _write_helmfile(tmp_path, content)
        g = OntologyGraph()
        HelmfileCollector(helmfile_path=path, environment="production").collect(g)

        release = next(
            (e for e in g.entities() if isinstance(e, HelmRelease) and e.name == "api"),
            None,
        )
        assert release is not None
        neighbour_uids = {e.uid for e in g.neighbors(release.uid)}
        assert "helmfile-env-production" in neighbour_uids

    def test_hosted_by_edge_wired_for_repo_chart(self, tmp_path):
        content = {
            "repositories": [
                {"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"},
            ],
            "releases": [
                {"name": "redis", "namespace": "default", "chart": "bitnami/redis"},
            ],
        }
        path = _write_helmfile(tmp_path, content)
        g = OntologyGraph()
        HelmfileCollector(helmfile_path=path).collect(g)

        release = next(
            (e for e in g.entities() if isinstance(e, HelmRelease) and e.name == "redis"),
            None,
        )
        assert release is not None
        neighbour_uids = {e.uid for e in g.neighbors(release.uid)}
        assert "helmrepo-bitnami" in neighbour_uids

    def test_no_hosted_by_for_local_chart(self, tmp_path):
        content = {
            "releases": [
                {"name": "myapp", "namespace": "default", "chart": "./charts/myapp"},
            ],
        }
        path = _write_helmfile(tmp_path, content)
        g = OntologyGraph()
        HelmfileCollector(helmfile_path=path).collect(g)

        release = next(
            (e for e in g.entities() if isinstance(e, HelmRelease) and e.name == "myapp"),
            None,
        )
        assert release is not None
        neighbour_uids = {e.uid for e in g.neighbors(release.uid)}
        repo_neighbours = [uid for uid in neighbour_uids if uid.startswith("helmrepo-")]
        assert repo_neighbours == []


# ─────────────────────────────────────────────────────────────────────────────
# Fixture graph — new ontology nodes present
# ─────────────────────────────────────────────────────────────────────────────

class TestSyntheticGraphOntology:
    def test_bitnami_repo_in_graph(self, synthetic_graph):
        assert synthetic_graph.get("helmrepo-bitnami") is not None

    def test_production_env_in_graph(self, synthetic_graph):
        env = synthetic_graph.get("helmfile-env-production")
        assert env is not None
        assert env.kube_context == "k3s-production"

    def test_sub_charts_hosted_by_bitnami(self, synthetic_graph):
        pg_neighbour_uids = {e.uid for e in synthetic_graph.neighbors("chart-postgresql-13.2.0")}
        assert "helmrepo-bitnami" in pg_neighbour_uids

    def test_db_release_deploys_in_production(self, synthetic_graph):
        neighbour_uids = {e.uid for e in synthetic_graph.neighbors("helm-production-db")}
        assert "helmfile-env-production" in neighbour_uids

    def test_env_to_text_indexed(self, synthetic_graph):
        env = synthetic_graph.get("helmfile-env-production")
        text = env.to_text()
        assert "production" in text
        assert "k3s-production" in text
