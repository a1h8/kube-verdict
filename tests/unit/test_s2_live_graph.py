"""S2's graph is the product's (docs/s2-correlation-cases.md, step 0, option B): what the test loader missed
is there, each check next to the loader graph that lacks it."""
from __future__ import annotations

from pathlib import Path

import pytest

from ontology.relationships import RelationshipType
from tests.integration.cases.case_loader import build_graph, load_case
from tests.s2.live_graph import build_graph_live

CASES = Path(__file__).resolve().parents[2] / "tests/s2/cases"
NAMES = sorted(d.name for d in CASES.iterdir() if d.is_dir())


@pytest.fixture(scope="module")
def graphs():
    return {n: build_graph_live(CASES / n) for n in NAMES}


def _count(graph, kind: str) -> int:
    return sum(1 for e in graph.entities() if e.kind.value == kind)


def _edges(graph, rel) -> int:
    return sum(1 for e in graph._edges if e.rel_type == rel)


def test_every_s2_case_is_built_by_the_collectors(graphs):
    assert len(graphs) == 21
    for name, g in graphs.items():
        assert _count(g, "Pod") and _count(g, "Namespace") >= 3, name


def test_services_and_their_pods_are_linked_where_the_loader_had_none(graphs):
    for name, g in graphs.items():
        assert _count(g, "Service") and _edges(g, RelationshipType.EXPOSES), name
    old = build_graph(load_case(CASES / "d1_dependency_service_wrong_port"))
    assert _count(old, "Service") == 0                                        # control: what the loader built


def test_configmaps_exist_so_none_is_reported_absent(graphs):
    for name in ("q0_quota_no_incident", "q1_quota_filled_by_request_drift", "q2_quota_filled_by_stuck_jobs"):
        assert _count(graphs[name], "ConfigMap") == 2, name


def test_an_image_drift_is_seen_as_the_product_sees_it(graphs):
    cart = next(e for e in graphs["q1_quota_filled_by_request_drift"].entities()
                if e.kind.value == "Deployment" and e.name == "cart-api")
    assert "applied.container.cart-api.image" in cart.annotations              # the decoy, now visible
    old = build_graph(load_case(CASES / "q1_quota_filled_by_request_drift"))
    old_cart = next(e for e in old.entities() if e.kind.value == "Deployment" and e.name == "cart-api")
    assert "applied.container.cart-api.image" not in old_cart.annotations       # control


def test_the_webhook_is_a_node(graphs):
    for name in ("w0_webhook_no_incident", "w1_webhook_injects_missing_image", "w2_webhook_fails_closed_expired_cert"):
        assert _count(graphs[name], "MutatingWebhook") == 1, name
    assert _count(graphs["q1_quota_filled_by_request_drift"], "MutatingWebhook") == 0   # control: none declared


def test_kinds_only_discovery_collects_are_in_the_graph(graphs):
    """Added 2026-11-08: discovery is served from the case's objects, as production runs it (step 2 of
    K8sCollector.collect). S2's first run had an empty discovery, so none of these was in any graph."""
    assert _count(graphs["l1_limitrange_default_injected"], "LimitRange") == 1
    assert _count(graphs["q1_quota_filled_by_request_drift"], "ResourceQuota") == 1
    assert _count(graphs["a2_hpa_scale_up_policy_slowed"], "HorizontalPodAutoscaler") == 1
    assert _count(graphs["q2_quota_filled_by_stuck_jobs"], "CronJob") == 1
    assert _count(graphs["q2_quota_filled_by_stuck_jobs"], "Job") == 25


def test_the_product_s_limits_stay_visible(graphs):
    # under a namespace filter discovery skips cluster-scoped kinds (APIServerDiscovery.fetch_resources),
    # and Secrets are never collected: a1's and w2's cause objects are absent, as they would be live
    assert _count(graphs["a1_hpa_custom_metrics_api_points_away"], "APIService") == 0
    assert _count(graphs["w2_webhook_fails_closed_expired_cert"], "Secret") == 0


def test_an_empty_discovery_loses_them(monkeypatch):
    """The control: with discovery answering nothing, as in S2's first run, the LimitRange is gone."""
    import tests.s2.live_graph as lg
    real = lg.discovery_for_case
    monkeypatch.setattr(lg, "discovery_for_case", lambda docs: real({}))
    g = lg.build_graph_live(CASES / "l1_limitrange_default_injected")
    assert _count(g, "LimitRange") == 0
