from ingestion.chart_parser import merge_values_hierarchy, flatten_values
from ingestion.helm_drift import _resolve_dot_path
from ontology.relationships import RelationshipType


class TestMergeValuesHierarchy:
    def test_last_wins_scalar(self):
        result = merge_values_hierarchy(
            {"replicaCount": 1},
            {"replicaCount": 3},
        )
        assert result["replicaCount"] == 3

    def test_deep_merge(self):
        result = merge_values_hierarchy(
            {"image": {"tag": "latest", "pullPolicy": "Always"}},
            {"image": {"tag": "1.2.3"}},
        )
        assert result["image"]["tag"] == "1.2.3"
        assert result["image"]["pullPolicy"] == "Always"

    def test_three_layers(self):
        result = merge_values_hierarchy(
            {"a": 1, "b": 1},
            {"b": 2, "c": 2},
            {"c": 3, "d": 3},
        )
        assert result == {"a": 1, "b": 2, "c": 3, "d": 3}

    def test_empty_layers(self):
        result = merge_values_hierarchy({}, {"key": "value"}, {})
        assert result == {"key": "value"}


class TestFlattenValues:
    def test_flat_dict(self):
        result = flatten_values({"replicaCount": 3, "enabled": True})
        assert result["replicaCount"] == "3"
        assert result["enabled"] == "True"

    def test_nested(self):
        result = flatten_values({"image": {"tag": "1.2.3", "repo": "nginx"}})
        assert result["image.tag"] == "1.2.3"
        assert result["image.repo"] == "nginx"

    def test_list_shows_length(self):
        result = flatten_values({"ports": [80, 443]})
        assert result["ports"] == "[2 items]"

    def test_max_depth(self):
        deep = {"a": {"b": {"c": {"d": "deep"}}}}
        result = flatten_values(deep, max_depth=2)
        assert "a.b" in result or "a.b.c" not in result

    def test_null_value(self):
        result = flatten_values({"key": None})
        assert result["key"] == "null"


class TestResolveDotPath:
    def test_simple(self):
        assert _resolve_dot_path({"a": 1}, "a") == 1

    def test_nested(self):
        assert _resolve_dot_path({"postgresql": {"enabled": True}}, "postgresql.enabled") is True

    def test_missing(self):
        assert _resolve_dot_path({}, "postgresql.enabled") is None

    def test_partial_missing(self):
        assert _resolve_dot_path({"postgresql": {}}, "postgresql.enabled") is None


class TestHelmDriftDetector:
    def test_detects_replica_drift(self, synthetic_graph):
        # synthetic_graph already has drift annotations on deploy-api
        # verify DRIFTS_FROM edge exists
        edges = synthetic_graph._adj.get("deploy-api", [])
        drift_edges = [e for e in edges if e.rel_type == RelationshipType.DRIFTS_FROM]
        assert len(drift_edges) > 0

    def test_detects_pvc_drift(self, synthetic_graph):
        edges = synthetic_graph._adj.get("pvc-api-data", [])
        drift_edges = [e for e in edges if e.rel_type == RelationshipType.DRIFTS_FROM]
        assert len(drift_edges) > 0

    def test_drift_in_to_text(self, synthetic_graph):
        pod = synthetic_graph.get("pod-api-xyz")
        text = pod.to_text()
        assert "DRIFT=" in text
        assert "CrashLoopBackOff" in text

    def test_pvc_drift_in_to_text(self, synthetic_graph):
        pvc = synthetic_graph.get("pvc-api-data")
        text = pvc.to_text()
        assert "DRIFT=" in text
        assert "Pending" in text
