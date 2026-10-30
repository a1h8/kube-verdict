"""A pod's workload is read from what runs it, not from its name — docs/fix-plan-001.md, H25
(proved: Pod/production/api-gateway-… was "the same workload" as HelmRelease/production/api)."""
from __future__ import annotations

from ontology.entities import Deployment, HelmRelease, Pod, ReplicaSet
from ontology.graph import OntologyGraph
from rca.signals import extract_signals

POD = "Pod/production/api-gateway-7d9f-x"


def _graph(with_rs=True, owner=True):
    g = OntologyGraph()
    for name in ("api", "api-gateway"):
        g.add_entity(HelmRelease(uid=f"rel-{name}", name=name, namespace="production"))
        g.add_entity(Deployment(uid=f"dep-{name}", name=name, namespace="production",
                                labels={"app.kubernetes.io/instance": name},
                                selector={"app": name}))
    if with_rs:
        g.add_entity(ReplicaSet(uid="rs", name="api-gateway-7d9f", namespace="production",
                                owner_ref_name="api-gateway"))
    g.add_entity(Pod(uid="p", name="api-gateway-7d9f-x", namespace="production", phase="Running",
                     labels={"app": "api-gateway", "app.kubernetes.io/instance": "api-gateway",
                             "pod-template-hash": "7d9f"},
                     owner_ref_kind="ReplicaSet" if owner else "",
                     owner_ref_name="api-gateway-7d9f" if owner else ""))
    return g


def test_a_name_prefix_is_not_a_workload():
    ss = extract_signals(_graph())
    assert not ss.same_workload(POD, "HelmRelease/production/api")
    assert not ss.same_workload(POD, "Deployment/production/api")


def test_owners_selector_and_release_are():                              # control
    for with_rs, owner in ((True, True), (False, True), (False, False)):
        ss = extract_signals(_graph(with_rs, owner))
        assert ss.same_workload(POD, "Deployment/production/api-gateway"), (with_rs, owner)
        assert ss.same_workload(POD, "HelmRelease/production/api-gateway"), (with_rs, owner)


def test_the_collector_links_replicaset_to_pod():
    from ingestion.k8s_collector import K8sCollector
    from unittest.mock import MagicMock
    from kubernetes import client as k8s
    g = OntologyGraph()
    g.add_entity(Pod(uid="p", name="api-7d9f-x", namespace="prod",
                     owner_ref_kind="ReplicaSet", owner_ref_name="api-7d9f"))
    c = K8sCollector.__new__(K8sCollector)
    c._apps = MagicMock()
    c._apps.list_namespaced_replica_set.return_value.items = [k8s.V1ReplicaSet(
        metadata=k8s.V1ObjectMeta(name="api-7d9f", namespace="prod", uid="rs"),
        spec=k8s.V1ReplicaSetSpec(replicas=1, selector=k8s.V1LabelSelector(match_labels={})),
        status=k8s.V1ReplicaSetStatus(replicas=1, ready_replicas=1))]
    c._find_by_name = lambda *a: None
    c._collect_replicasets(g, "prod")
    assert "p" in {e.uid for e in g.neighbors("rs")}
