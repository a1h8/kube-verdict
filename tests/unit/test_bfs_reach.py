"""From a sick pod the walk reaches what owns, exposes and constrains it, and the evidence
attached to it — docs/fix-plan-001.md, H37 (it reached only the Helm release)."""
from __future__ import annotations

from dedup.bfs import expand_incident_context
from ontology.entities import (Deployment, Namespace, Pod, PrometheusAlert, ReplicaSet,
                               ResourceQuota, Service)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType as R


def _graph():
    g = OntologyGraph()
    for e in (Namespace(uid="ns", name="prod"), Deployment(uid="dep", name="api", namespace="prod"),
              ReplicaSet(uid="rs", name="api-7d9f", namespace="prod"),
              Pod(uid="pod", name="api-7d9f-x", namespace="prod", phase="Pending"),
              Service(uid="svc", name="api", namespace="prod"),
              ResourceQuota(uid="quota", name="compute", namespace="prod"),
              PrometheusAlert(uid="alert", name="KubePodNotReady", namespace="prod"),
              Pod(uid="other", name="web-1", namespace="prod", phase="Running")):
        g.add_entity(e)
    for src, dst, rel in (("dep", "rs", R.OWNS), ("rs", "pod", R.OWNS), ("svc", "pod", R.EXPOSES),
                          ("pod", "ns", R.IN_NAMESPACE), ("other", "ns", R.IN_NAMESPACE),
                          ("quota", "ns", R.QUOTA_BLOCKS), ("pod", "alert", R.HAS_ALERT)):
        g.add_edge(Edge(src, dst, rel))
    return g


def test_the_walk_reaches_owners_service_quota_and_evidence():
    g = _graph()
    reached = {e.uid for e in expand_incident_context(g, seeds=[g.get("pod")], max_depth=3)}
    assert {"rs", "dep", "svc", "ns", "quota", "alert"} <= reached


def test_the_walk_does_not_pull_the_whole_namespace():                  # control
    g = _graph()
    reached = {e.uid for e in expand_incident_context(g, seeds=[g.get("pod")], max_depth=3)}
    assert "other" not in reached
