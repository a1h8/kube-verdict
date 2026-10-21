"""NetworkPolicies collected and read — docs/failing-cases-002.md S4 (h006)."""
from __future__ import annotations

from unittest.mock import MagicMock

from kubernetes import client as k8s

from ingestion.k8s_collector import K8sCollector
from ingestion.readiness import Present, detect_missing
from ontology.entities import LokiLog, Pod
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType as R
from rca.diagnosis import diagnose
from rca.signals import extract_signals

POD_API = {"metadata": {"name": "orders-1", "namespace": "prod", "labels": {"app": "orders"}}, "spec": {}}


def _policy(egress):
    spec = {"podSelector": {"matchLabels": {"app": "orders"}}, "policyTypes": ["Egress"]}
    if egress is not None:
        spec["egress"] = egress
    return {"metadata": {"name": "restrict", "namespace": "prod"}, "spec": spec}


def _outcome(policy, dns_failure=True):
    pod = Pod(uid="p", name="orders-1", namespace="prod", phase="Running", labels={"app": "orders"},
              container_statuses=[{"name": "c", "ready": False, "state": {"running": {}}}])
    pod.annotations.update(detect_missing(POD_API, Present(networkpolicies=[policy])))
    g = OntologyGraph(); g.add_entity(pod)
    if dns_failure:
        g.add_entity(LokiLog(uid="l", name="l", namespace="prod", pod_name="orders-1", level="error",
                             log_line='err="dial tcp: lookup db.prod.svc.cluster.local: i/o timeout"'))
        g.add_edge(Edge("p", "l", R.HAS_LOG))
    return diagnose(extract_signals(g))


def test_an_empty_egress_policy_with_dns_failures_is_the_cause():
    d = _outcome(_policy([])).diagnoses[0]
    assert (d.rule, d.cause_location) == ("netpol.egress_blocked", "NetworkPolicy/prod/restrict")


def test_a_policy_that_allows_egress_is_not_the_cause():                             # control
    allowed = _policy([{"ports": [{"port": 53, "protocol": "UDP"}, {"port": 5432}]}])
    assert not any(d.rule == "netpol.egress_blocked" for d in _outcome(allowed).diagnoses)


def test_a_blocking_policy_with_nothing_failing_is_no_incident():                    # control
    assert not any(d.rule == "netpol.egress_blocked" for d in _outcome(_policy([]), dns_failure=False).diagnoses)


def test_the_collector_lists_networkpolicies_and_records_a_refusal():
    c = K8sCollector.__new__(K8sCollector)
    c._listing_failed = set(); c._api_client = k8s.ApiClient()
    c._netpol_api = MagicMock()
    c._netpol_api.list_namespaced_network_policy.return_value.items = [k8s.V1NetworkPolicy(
        metadata=k8s.V1ObjectMeta(name="restrict", namespace="prod"),
        spec=k8s.V1NetworkPolicySpec(pod_selector=k8s.V1LabelSelector(match_labels={"app": "orders"}),
                                     policy_types=["Egress"], egress=[]))]
    c._collect_networkpolicies("prod")
    assert c._netpols["prod"][0]["metadata"]["name"] == "restrict"
    c._netpol_api.list_namespaced_network_policy.side_effect = k8s.ApiException(status=403)
    c._collect_networkpolicies("dev")
    assert ("dev", "networkpolicies") in c._listing_failed
