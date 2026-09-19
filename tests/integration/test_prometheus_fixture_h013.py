"""
h013 — SLO error-budget burn (the Prometheus-fixture wedge).

Unlike h001–h012, nothing is wrong at the Kubernetes level: 3/3 replicas Ready,
zero restarts, no warning events, no Helm drift. The only evidence is the
Prometheus SLO alerts (prometheus/alerts.json) — multi-window burn-rate alerts
and a p99/p95 latency-SLO breach whose description isolates the increase to a
dependency call. case_loader feeds them through the REAL PrometheusCollector
(HTTP fetch swapped for the fixture list), so correlation and annotation shape
match a live cluster.

The fixture also carries two decoys the real collector must drop: a `pending`
alert and a firing alert for a deployment absent from the snapshot.

Deterministic — no cluster, no Ollama, no FAISS embedding (stub store).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontology.entities import Deployment, Pod, PrometheusAlert, ResourceKind
from ontology.relationships import RelationshipType
from rca.context_builder import ContextBuilder
from tests.integration.cases.case_loader import build_graph, load_case

CASES_ROOT = Path(__file__).parent / "cases"
CASE_DIR = CASES_ROOT / "h013_slo_error_budget_burn"

FIRING_NAMES = {"SLOErrorBudgetBurnFast", "SLOErrorBudgetBurnSlow", "LatencySLOBreach"}


class _StubStore:
    """No FAISS/embedding needed — this case exercises alert wiring only."""

    last_retrieval_stats: dict = {}

    def search(self, query, top_k=10):
        return []

    def hybrid_search(self, query, top_k=10):
        return []


@pytest.fixture(scope="module")
def graph():
    return build_graph(load_case(CASE_DIR))


@pytest.fixture(scope="module")
def alerts(graph):
    return [
        e for e in graph.entities(ResourceKind.PROMETHEUS_ALERT)
        if isinstance(e, PrometheusAlert)
    ]


@pytest.fixture(scope="module")
def deployment(graph):
    deps = [e for e in graph.entities() if isinstance(e, Deployment)]
    assert len(deps) == 1
    return deps[0]


@pytest.fixture(scope="module")
def pod(graph):
    pods = [e for e in graph.entities() if isinstance(e, Pod)]
    assert len(pods) == 1
    return pods[0]


@pytest.fixture(scope="module")
def ctx(graph):
    return ContextBuilder(graph=graph, store=_StubStore()).build("why is checkout-api slow")


class TestFixtureLoadingAndCollectorFiltering:
    def test_fixture_declares_five_raw_alerts(self):
        raw = json.loads((CASE_DIR / "prometheus" / "alerts.json").read_text())
        assert len(raw) == 5
        assert {"labels", "annotations", "state", "activeAt"} <= set(raw[0])

    def test_only_the_three_correlated_firing_alerts_become_nodes(self, alerts):
        assert {a.alert_name for a in alerts} == FIRING_NAMES

    def test_all_nodes_are_firing(self, alerts):
        assert all(a.state == "firing" for a in alerts)

    def test_pending_alert_is_dropped(self, alerts):
        assert "LatencyP95Pending" not in {a.alert_name for a in alerts}

    def test_uncorrelated_alert_is_dropped(self, alerts):
        """The reporting-batch decoy shares an alertname with the real Fast
        alert; only the checkout-api one may survive."""
        fast = [a for a in alerts if a.alert_name == "SLOErrorBudgetBurnFast"]
        assert len(fast) == 1
        assert fast[0].alert_labels["deployment"] == "checkout-api"
        assert fast[0].alert_labels["burn_rate"] == "14.4"


class TestKubernetesLooksHealthy:
    """The whole point of h013: every status-only check is green."""

    def test_pod_is_not_unhealthy(self, pod):
        assert not pod.is_unhealthy

    def test_deployment_is_not_degraded(self, deployment):
        assert not deployment.is_degraded

    def test_no_seeds_no_drift_no_events_in_context(self, ctx):
        assert ctx.seeds == []
        assert ctx.drift == []
        assert ctx.events == []


class TestGraphWiring:
    def test_has_alert_edges_from_deployment(self, graph, deployment):
        linked = graph.neighbors(deployment.uid, RelationshipType.HAS_ALERT)
        assert {n.alert_name for n in linked} == FIRING_NAMES

    def test_deployment_annotated_with_alert_severity(self, deployment):
        assert deployment.annotations["alert.SLOErrorBudgetBurnFast.severity"] == "critical"
        assert deployment.annotations["alert.SLOErrorBudgetBurnSlow.severity"] == "warning"
        assert deployment.annotations["alert.LatencySLOBreach.severity"] == "warning"

    def test_alerts_correlate_to_the_deployment_not_the_pod(self, graph, pod):
        """Alert labels carry only `deployment`, so the pod gets no HAS_ALERT."""
        assert graph.neighbors(pod.uid, RelationshipType.HAS_ALERT) == []


class TestContextWindowSurfacesAlerts:
    def test_alerts_section_populated(self, ctx):
        assert len(ctx.alerts) == 3

    def test_critical_alert_comes_first(self, ctx):
        assert "SLOErrorBudgetBurnFast" in ctx.alerts[0]
        assert "severity=critical" in ctx.alerts[0]

    def test_prompt_block_carries_the_slo_evidence(self, ctx):
        prompt = ctx.to_prompt_block()
        assert "Firing Prometheus alerts (3)" in prompt
        assert "14.4x" in prompt          # burn rate from the Fast alert summary
        assert "1.84s" in prompt          # p99 from the latency alert
        assert "payments-gateway" in prompt  # the dependency the latency is isolated to


class TestLoaderStaysBackwardCompatible:
    def test_cases_without_prometheus_dir_load_no_alerts(self):
        case = load_case(CASES_ROOT / "h001_crashloopbackoff")
        assert case["prometheus_alerts"] == []

    def test_cases_without_prometheus_dir_add_no_alert_nodes(self):
        graph = build_graph(load_case(CASES_ROOT / "h001_crashloopbackoff"))
        assert list(graph.entities(ResourceKind.PROMETHEUS_ALERT)) == []
