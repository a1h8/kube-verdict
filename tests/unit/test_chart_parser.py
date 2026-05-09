from pathlib import Path

import pytest
import yaml

from ingestion.chart_parser import ChartParser


@pytest.fixture
def chart_dir(tmp_path) -> Path:
    """Creates a minimal chart directory on disk."""
    chart_yaml = {
        "apiVersion": "v2",
        "name": "my-app",
        "version": "1.0.0",
        "description": "Test chart",
        "type": "application",
        "dependencies": [],
    }
    values_yaml = {
        "replicaCount": 2,
        "image": {"repository": "nginx", "tag": "1.21"},
        "service": {"type": "ClusterIP", "port": 80},
        "persistence": {"enabled": True, "size": "5Gi"},
    }
    (tmp_path / "Chart.yaml").write_text(yaml.dump(chart_yaml))
    (tmp_path / "values.yaml").write_text(yaml.dump(values_yaml))
    return tmp_path


@pytest.fixture
def umbrella_chart_dir(tmp_path) -> Path:
    """Creates an umbrella chart with a postgresql sub-dependency."""
    chart_yaml = {
        "apiVersion": "v2",
        "name": "umbrella",
        "version": "2.0.0",
        "description": "Umbrella chart",
        "dependencies": [
            {"name": "postgresql", "version": "12.0.0",
             "repository": "https://charts.bitnami.com/bitnami",
             "condition": "postgresql.enabled"},
        ],
    }
    values_yaml = {
        "replicaCount": 1,
        "postgresql": {
            "enabled": True,
            "auth": {"password": "", "database": "myapp"},
        },
    }
    (tmp_path / "Chart.yaml").write_text(yaml.dump(chart_yaml))
    (tmp_path / "values.yaml").write_text(yaml.dump(values_yaml))
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()

    # Sub-chart
    sub = charts_dir / "postgresql"
    sub.mkdir()
    (sub / "Chart.yaml").write_text(yaml.dump(
        {"apiVersion": "v2", "name": "postgresql", "version": "12.0.0"}
    ))
    (sub / "values.yaml").write_text(yaml.dump(
        {"auth": {"password": "changeme", "database": "postgres"},
         "primary": {"persistence": {"size": "8Gi"}}}
    ))
    return tmp_path


class TestChartParserFromDir:
    def test_reads_name_and_version(self, chart_dir):
        chart = ChartParser().from_dir(chart_dir)
        assert chart is not None
        assert chart.name == "my-app"
        assert chart.chart_version == "1.0.0"

    def test_reads_default_values(self, chart_dir):
        chart = ChartParser().from_dir(chart_dir)
        assert chart.default_values["replicaCount"] == 2
        assert chart.default_values["image"]["tag"] == "1.21"

    def test_not_umbrella_without_charts_dir(self, chart_dir):
        chart = ChartParser().from_dir(chart_dir)
        assert not chart.is_umbrella

    def test_uid_format(self, chart_dir):
        chart = ChartParser().from_dir(chart_dir)
        assert chart.uid == "chart-my-app-1.0.0"

    def test_missing_chart_yaml_returns_none(self, tmp_path):
        assert ChartParser().from_dir(tmp_path) is None


class TestUmbrellaChart:
    def test_detects_umbrella(self, umbrella_chart_dir):
        chart = ChartParser().from_dir(umbrella_chart_dir)
        assert chart.is_umbrella

    def test_dependencies_parsed(self, umbrella_chart_dir):
        chart = ChartParser().from_dir(umbrella_chart_dir)
        assert len(chart.dependencies) == 1
        dep = chart.dependencies[0]
        assert dep.name == "postgresql"
        assert dep.condition == "postgresql.enabled"

    def test_sub_charts_parsed(self, umbrella_chart_dir):
        chart = ChartParser().from_dir(umbrella_chart_dir)
        sub_charts = getattr(chart, "_sub_charts", [])
        assert len(sub_charts) == 1
        assert sub_charts[0].name == "postgresql"

    def test_parent_values_override_sub_chart(self, umbrella_chart_dir):
        chart = ChartParser().from_dir(umbrella_chart_dir)
        sub = getattr(chart, "_sub_charts", [])[0]
        # Parent sets postgresql.auth.database = "myapp"
        # Sub-chart default is "postgres"
        assert sub.default_values["auth"]["database"] == "myapp"

    def test_sub_chart_default_preserved_if_not_overridden(self, umbrella_chart_dir):
        chart = ChartParser().from_dir(umbrella_chart_dir)
        sub = getattr(chart, "_sub_charts", [])[0]
        # primary.persistence.size not overridden by parent
        assert sub.default_values["primary"]["persistence"]["size"] == "8Gi"
