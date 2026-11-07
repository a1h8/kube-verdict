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
    assert len(graphs) == 18
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
