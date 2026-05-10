from pathlib import Path
import yaml

from ingestion.helmfile_collector import HelmfileCollector, _deep_merge, _set_nested
from ontology.entities import HelmRelease


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_helmfile(tmp_path: Path, content: dict) -> Path:
    p = tmp_path / "helmfile.yaml"
    p.write_text(yaml.dump(content))
    return p


# ---------------------------------------------------------------------------
# Unit tests — helpers
# ---------------------------------------------------------------------------

class TestDeepMerge:
    def test_scalar_override(self):
        base = {"a": 1}
        _deep_merge(base, {"a": 2})
        assert base["a"] == 2

    def test_nested_merge(self):
        base = {"image": {"tag": "latest", "pull": "Always"}}
        _deep_merge(base, {"image": {"tag": "1.0"}})
        assert base["image"]["tag"] == "1.0"
        assert base["image"]["pull"] == "Always"  # preserved

    def test_new_key(self):
        base = {"a": 1}
        _deep_merge(base, {"b": 2})
        assert base == {"a": 1, "b": 2}


class TestSetNested:
    def test_simple(self):
        d = {}
        _set_nested(d, "key", "value")
        assert d == {"key": "value"}

    def test_dotted(self):
        d = {}
        _set_nested(d, "image.tag", "1.0")
        assert d["image"]["tag"] == "1.0"

    def test_triple_dotted(self):
        d = {}
        _set_nested(d, "a.b.c", 42)
        assert d["a"]["b"]["c"] == 42

    def test_overwrites_existing(self):
        d = {"image": {"tag": "old"}}
        _set_nested(d, "image.tag", "new")
        assert d["image"]["tag"] == "new"


# ---------------------------------------------------------------------------
# HelmfileCollector — YAML parsing
# ---------------------------------------------------------------------------

class TestHelmfileCollector:
    def test_collect_adds_entities(self, tmp_path, synthetic_graph):
        helmfile_content = {
            "releases": [
                {"name": "my-api", "namespace": "default",
                 "chart": "myrepo/my-api", "version": "1.0.0"},
            ]
        }
        path = write_helmfile(tmp_path, helmfile_content)
        hf = HelmfileCollector(helmfile_path=path, environment="default")
        hf.collect(synthetic_graph)
        entity = next(
            (e for e in synthetic_graph.entities()
             if e.name == "my-api" and getattr(e, "source", "") == "helmfile"),
            None,
        )
        assert entity is not None

    def test_environment_values_merged(self, tmp_path, synthetic_graph):
        env_values = tmp_path / "env-prod.yaml"
        env_values.write_text(yaml.dump({"replicaCount": 5}))

        helmfile_content = {
            "environments": {
                "production": {"values": ["env-prod.yaml"]},
            },
            "releases": [
                {"name": "api", "namespace": "production",
                 "chart": "myrepo/api", "version": "1.0.0"},
            ],
        }
        path = write_helmfile(tmp_path, helmfile_content)
        hf = HelmfileCollector(helmfile_path=path, environment="production")
        hf.collect(synthetic_graph)
        entity = next(
            (e for e in synthetic_graph.entities()
             if e.name == "api" and getattr(e, "source", "") == "helmfile"),
            None,
        )
        assert entity is not None
        assert entity.values.get("replicaCount") == 5

    def test_set_values_applied(self, tmp_path, synthetic_graph):
        helmfile_content = {
            "releases": [
                {"name": "redis", "namespace": "default",
                 "chart": "bitnami/redis", "version": "17.0.0",
                 "set": [{"name": "auth.enabled", "value": False}]},
            ]
        }
        path = write_helmfile(tmp_path, helmfile_content)
        hf = HelmfileCollector(helmfile_path=path)
        hf.collect(synthetic_graph)
        entity = next(
            (e for e in synthetic_graph.entities()
             if e.name == "redis" and isinstance(e, HelmRelease)), None
        )
        assert entity is not None
        assert entity.values.get("auth", {}).get("enabled") is False

    def test_needs_wires_depends_on(self, tmp_path, synthetic_graph):
        from ontology.relationships import RelationshipType
        helmfile_content = {
            "releases": [
                {"name": "postgres", "namespace": "db", "chart": "bitnami/postgresql"},
                {"name": "api", "namespace": "default", "chart": "myrepo/api",
                 "needs": ["db/postgres"]},
            ]
        }
        path = write_helmfile(tmp_path, helmfile_content)
        hf = HelmfileCollector(helmfile_path=path)
        hf.collect(synthetic_graph)

        api_entity = next(
            (e for e in synthetic_graph.entities()
             if e.name == "api" and getattr(e, "source", "") == "helmfile"), None
        )
        assert api_entity is not None
        edges = synthetic_graph._adj.get(api_entity.uid, [])
        dep_edges = [e for e in edges if e.rel_type == RelationshipType.DEPENDS_ON]
        assert len(dep_edges) == 1

    def test_missing_path_skips_silently(self, synthetic_graph):
        hf = HelmfileCollector(helmfile_path="/nonexistent/helmfile.yaml")
        hf.collect(synthetic_graph)  # should not raise

    def test_helmfile_dir_mode(self, tmp_path, synthetic_graph):
        d = tmp_path / "helmfile.d"
        d.mkdir()
        (d / "01-api.yaml").write_text(yaml.dump({
            "releases": [{"name": "svc-a", "namespace": "default", "chart": "repo/a"}]
        }))
        (d / "02-db.yaml").write_text(yaml.dump({
            "releases": [{"name": "svc-b", "namespace": "default", "chart": "repo/b"}]
        }))
        hf = HelmfileCollector(helmfile_path=d)
        hf.collect(synthetic_graph)
        names = {e.name for e in synthetic_graph.entities()
                 if getattr(e, "source", "") == "helmfile"}
        assert "svc-a" in names
        assert "svc-b" in names
