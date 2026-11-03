"""An object is linked to the release that deployed it, not to every release of its
namespace — docs/fix-plan-001.md, H26 (proved: two healthy releases made two false replica
drifts; likely cause of D2.1 on h016)."""
from __future__ import annotations

from ingestion.helm_collector import HelmCollector, belongs_to_release
from ingestion.helm_drift import HelmDriftDetector
from ontology.entities import Deployment, HelmRelease, Pod
from ontology.graph import OntologyGraph


def _dep(name, replicas, annotated=False):
    labels = {"app.kubernetes.io/managed-by": "Helm", "app.kubernetes.io/instance": name}
    ann = {"meta.helm.sh/release-name": name} if annotated else {}
    return Deployment(uid=f"dep-{name}", name=name, namespace="prod", labels=labels,
                      k8s_annotations=ann, replicas=replicas, ready_replicas=replicas,
                      available_replicas=replicas)


def _graph(annotated):
    g = OntologyGraph()
    for name, n in (("orders", 3), ("catalog", 2)):
        g.add_entity(HelmRelease(uid=f"rel-{name}", name=name, namespace="prod",
                                 values={"replicaCount": n}, status="deployed"))
        g.add_entity(_dep(name, n, annotated))
    collector = HelmCollector.__new__(HelmCollector)
    for rel in [e for e in g.entities() if isinstance(e, HelmRelease)]:
        collector._link_managed_resources(g, rel, "prod")
    return g


def _drifts(g):
    return {e.name: k for e in g.entities() for k in e.annotations if k.startswith("drift.")}


def test_two_healthy_releases_make_no_drift():
    for annotated in (False, True):
        g = _graph(annotated)
        HelmDriftDetector().detect_all(g)
        assert _drifts(g) == {}


def test_a_real_replica_drift_is_still_found():                         # control
    g = _graph(False)
    next(e for e in g.entities() if e.name == "orders" and isinstance(e, Deployment)).replicas = 1
    HelmDriftDetector().detect_all(g)
    assert _drifts(g) == {"orders": "drift.spec.replicas"}


def test_membership_rules():
    pod = Pod(uid="p", name="orders-7d9f-x", namespace="prod",
              labels={"app.kubernetes.io/managed-by": "Helm", "app.kubernetes.io/instance": "orders"})
    assert belongs_to_release(pod, "orders") and not belongs_to_release(pod, "catalog")
    other = Pod(uid="q", name="x", namespace="prod", labels={"app.kubernetes.io/managed-by": "Helm"})
    assert not belongs_to_release(other, "orders")          # managed-by alone names no release
    rel = HelmRelease(uid="r", name="orders", namespace="prod",
                      labels={"app.kubernetes.io/instance": "orders"})
    assert not belongs_to_release(rel, "orders")             # a release is not its own resource
