"""The logs of the workload the query names are read, Ready or not — docs/fix-plan-001.md H49,
docs/failing-cases-002.md S1 (h005 was `no_incident` while its application failed)."""
from __future__ import annotations

from ingestion.loki_source import LokiSource, pods_of_named_workloads
from ontology.entities import Deployment, Pod
from ontology.graph import OntologyGraph

READY = [{"name": "c", "ready": True, "state": {"running": {}}}]


def _graph():
    g = OntologyGraph()
    g.add_entity(Deployment(uid="d1", name="audit-exporter", namespace="mon", selector={"app": "audit-exporter"}))
    g.add_entity(Deployment(uid="d2", name="catalog-api", namespace="mon", selector={"app": "catalog-api"}))
    g.add_entity(Pod(uid="p1", name="audit-exporter-7b8d-x", namespace="mon", phase="Running",
                     labels={"app": "audit-exporter"}, container_statuses=READY))
    g.add_entity(Pod(uid="p2", name="catalog-api-5c9f-y", namespace="mon", phase="Running",
                     labels={"app": "catalog-api"}, container_statuses=READY))
    return g


def test_the_named_workload_pods_are_selected():
    assert pods_of_named_workloads(_graph(), "audit-exporter keeps crashing") == {"p1"}


def test_a_ready_pod_not_named_is_not_read():                                       # control
    queried = []
    src = LokiSource(url="http://x")
    src._query = lambda logql, s, e: queried.append(logql) or []
    src.collect(_graph(), query="audit-exporter keeps crashing")
    assert any("audit-exporter-7b8d-x" in q for q in queried)
    assert not any("catalog-api" in q for q in queried)
    queried.clear()
    src.collect(_graph(), query="")                                      # no name: Ready pods unread
    assert queried == []
