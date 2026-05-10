"""
Unit tests for MetricsServerCollector — K8s API calls are mocked.
"""
from unittest.mock import MagicMock, patch

import pytest

from ingestion.metrics_server_collector import (
    MetricsServerCollector,
    _parse_cpu_millicores,
    _parse_memory_mib,
)
from ontology.entities import Pod
from ontology.graph import OntologyGraph


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_collector() -> MetricsServerCollector:
    """Build a collector with the K8s config loading patched out."""
    with patch("ingestion.metrics_server_collector.k8s_config.load_kube_config"), \
         patch("ingestion.metrics_server_collector.k8s_client.ApiClient"), \
         patch("ingestion.metrics_server_collector.k8s_client.CustomObjectsApi"):
        c = MetricsServerCollector()
    return c


def _pod_metric(
    name: str,
    namespace: str,
    cpu: str = "125m",
    memory: str = "256Mi",
    containers: int = 1,
) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace},
        "containers": [
            {"name": f"c{i}", "usage": {"cpu": cpu, "memory": memory}}
            for i in range(containers)
        ],
    }


def _graph_with_pod(name: str = "api-0", namespace: str = "prod") -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Pod(uid="p1", name=name, namespace=namespace, phase="Running"))
    return g


# ─────────────────────────────────────────────────────────────────────────────
# CPU parser
# ─────────────────────────────────────────────────────────────────────────────

class TestParseCpuMillicores:
    def test_millicores(self):
        assert _parse_cpu_millicores("125m") == pytest.approx(125.0)

    def test_cores(self):
        assert _parse_cpu_millicores("1") == pytest.approx(1000.0)

    def test_half_core(self):
        assert _parse_cpu_millicores("0.5") == pytest.approx(500.0)

    def test_nanocores(self):
        assert _parse_cpu_millicores("1000000000n") == pytest.approx(1000.0)

    def test_zero(self):
        assert _parse_cpu_millicores("0") == pytest.approx(0.0)

    def test_empty(self):
        assert _parse_cpu_millicores("") == pytest.approx(0.0)

    def test_invalid(self):
        assert _parse_cpu_millicores("bad") == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Memory parser
# ─────────────────────────────────────────────────────────────────────────────

class TestParseMemoryMib:
    def test_mebibytes(self):
        assert _parse_memory_mib("256Mi") == pytest.approx(256.0)

    def test_gibibytes(self):
        assert _parse_memory_mib("1Gi") == pytest.approx(1024.0)

    def test_kibibytes(self):
        assert _parse_memory_mib("1024Ki") == pytest.approx(1.0)

    def test_tebibytes(self):
        assert _parse_memory_mib("1Ti") == pytest.approx(1024 * 1024.0)

    def test_raw_bytes(self):
        assert _parse_memory_mib("1048576") == pytest.approx(1.0)

    def test_megabytes(self):
        assert _parse_memory_mib("100M") == pytest.approx(100_000_000 / (1024 * 1024))

    def test_gigabytes(self):
        assert _parse_memory_mib("1G") == pytest.approx(1_000_000_000 / (1024 * 1024))

    def test_zero(self):
        assert _parse_memory_mib("0") == pytest.approx(0.0)

    def test_empty(self):
        assert _parse_memory_mib("") == pytest.approx(0.0)

    def test_invalid(self):
        assert _parse_memory_mib("bad") == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# MetricsServerCollector.collect
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricsServerCollectorCollect:
    def test_empty_graph_returns_zero(self):
        c = _make_collector()
        c._fetch_namespace = MagicMock(return_value=[])
        assert c.collect(OntologyGraph()) == 0

    def test_pod_annotated_with_cpu_and_memory(self):
        g = _graph_with_pod()
        c = _make_collector()
        c._fetch_namespace = MagicMock(return_value=[_pod_metric("api-0", "prod")])
        c.collect(g)
        pod = next(iter(g.entities()))
        assert "metrics.cpu_m" in pod.annotations
        assert "metrics.memory_mi" in pod.annotations

    def test_cpu_value_correct(self):
        g = _graph_with_pod()
        c = _make_collector()
        c._fetch_namespace = MagicMock(return_value=[_pod_metric("api-0", "prod", cpu="200m")])
        c.collect(g)
        pod = next(iter(g.entities()))
        assert float(pod.annotations["metrics.cpu_m"]) == pytest.approx(200.0)

    def test_memory_value_correct(self):
        g = _graph_with_pod()
        c = _make_collector()
        c._fetch_namespace = MagicMock(return_value=[_pod_metric("api-0", "prod", memory="512Mi")])
        c.collect(g)
        pod = next(iter(g.entities()))
        assert float(pod.annotations["metrics.memory_mi"]) == pytest.approx(512.0)

    def test_multi_container_sum(self):
        g = _graph_with_pod()
        c = _make_collector()
        # 2 containers × 100m cpu = 200m total
        c._fetch_namespace = MagicMock(
            return_value=[_pod_metric("api-0", "prod", cpu="100m", memory="128Mi", containers=2)]
        )
        c.collect(g)
        pod = next(iter(g.entities()))
        assert float(pod.annotations["metrics.cpu_m"]) == pytest.approx(200.0)
        assert float(pod.annotations["metrics.memory_mi"]) == pytest.approx(256.0)

    def test_unmatched_pod_skipped(self):
        g = _graph_with_pod("api-0", "prod")
        c = _make_collector()
        # Metric for a different pod
        c._fetch_namespace = MagicMock(
            return_value=[_pod_metric("other-pod", "prod")]
        )
        count = c.collect(g)
        assert count == 0
        pod = next(iter(g.entities()))
        assert "metrics.cpu_m" not in pod.annotations

    def test_namespace_mismatch_skipped(self):
        g = _graph_with_pod("api-0", "prod")
        c = _make_collector()
        c._fetch_namespace = MagicMock(
            return_value=[_pod_metric("api-0", "staging")]
        )
        count = c.collect(g)
        assert count == 0

    def test_returns_annotated_count(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="pod-a", namespace="prod", phase="Running"))
        g.add_entity(Pod(uid="p2", name="pod-b", namespace="prod", phase="Running"))
        c = _make_collector()
        c._fetch_namespace = MagicMock(return_value=[
            _pod_metric("pod-a", "prod"),
            _pod_metric("pod-b", "prod"),
        ])
        assert c.collect(g) == 2

    def test_no_namespace_uses_fetch_all(self):
        """Pod without namespace → _fetch_all() is called."""
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="pod-x", namespace=None, phase="Running"))
        c = _make_collector()
        c._fetch_all = MagicMock(return_value=[_pod_metric("pod-x", "")])
        c._fetch_namespace = MagicMock()
        c.collect(g)
        c._fetch_all.assert_called_once()
        c._fetch_namespace.assert_not_called()

    def test_api_exception_returns_zero(self):
        from kubernetes.client import ApiException
        g = _graph_with_pod()
        c = _make_collector()
        c._custom.list_namespaced_custom_object = MagicMock(
            side_effect=ApiException(status=503, reason="unavailable")
        )
        # Should not raise; returns 0
        assert c.collect(g) == 0

    def test_empty_metrics_result(self):
        g = _graph_with_pod()
        c = _make_collector()
        c._fetch_namespace = MagicMock(return_value=[])
        assert c.collect(g) == 0

    def test_multiple_namespaces_queries_each(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="a", namespace="ns1", phase="Running"))
        g.add_entity(Pod(uid="p2", name="b", namespace="ns2", phase="Running"))
        c = _make_collector()
        c._fetch_namespace = MagicMock(return_value=[])
        c.collect(g)
        called_ns = {call.args[0] for call in c._fetch_namespace.call_args_list}
        assert called_ns == {"ns1", "ns2"}
