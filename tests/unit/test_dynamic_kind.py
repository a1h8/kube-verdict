"""A discovered entity's kind answers ``.value`` like a typed one (found 2026-11-08: product code that reads
``entity.kind.value`` raised AttributeError on any entity added by API discovery)."""
from __future__ import annotations

from ingestion import manifest_differ, prometheus_collector
from ontology.discovery import APIResourceInfo
from ontology.dynamic_entity import DynamicKind, GenericEntity
from ontology.entities import Namespace
from ontology.graph import OntologyGraph

_LR = APIResourceInfo(group="", version="v1", kind="LimitRange", plural="limitranges", namespaced=True,
                      verbs=["list"])


def _graph() -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns-shop", name="shop"))
    g.add_entity(GenericEntity.from_api_object(
        {"metadata": {"name": "report-worker", "namespace": "shop", "uid": "lr-1"}, "spec": {}}, _LR))
    return g


def test_a_discovered_kind_is_its_string_and_has_a_value():
    e = next(x for x in _graph().entities() if x.uid == "lr-1")
    assert isinstance(e.kind, DynamicKind)
    assert e.kind == "LimitRange" and e.kind.value == "LimitRange" and str(e.kind) == "LimitRange"


def test_the_finders_that_read_kind_value_no_longer_raise():
    g = _graph()
    # same name as the applied Deployment would have: the differ reaches the generic entity first
    assert manifest_differ._find_entity(g, "Deployment", "report-worker", "shop") is None
    assert prometheus_collector._find_entity(g, "Deployment", "report-worker", "shop") is None
    assert manifest_differ._find_entity(g, "LimitRange", "report-worker", "shop").uid == "lr-1"


_RQ = APIResourceInfo(group="", version="v1", kind="ResourceQuota", plural="resourcequotas", namespaced=True,
                      verbs=["list"])


def test_a_discovered_quota_does_not_break_the_seed_search():
    """Its kind equals ResourceKind.RESOURCE_QUOTA (a str enum) but it has no typed fields: find_unhealthy
    raised AttributeError on any cluster holding a ResourceQuota."""
    from dedup.bfs import find_unhealthy
    g = _graph()
    g.add_entity(GenericEntity.from_api_object(
        {"metadata": {"name": "shop-quota", "namespace": "shop", "uid": "rq-1"}, "spec": {}}, _RQ))
    assert all(s.uid != "rq-1" for s in find_unhealthy(g))
