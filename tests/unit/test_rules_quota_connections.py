"""Rules S2 (quota) and S3 (connection exhaustion) — docs/failing-cases-002.md."""
from __future__ import annotations

from ontology.entities import Deployment, K8sEvent, LokiLog, Pod, PrometheusAlert
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType as R
from rca.diagnosis import diagnose
from rca.signals import extract_signals

QUOTA_MSG = ('Error creating: pods "w-7d8f9c6b5-" is forbidden: exceeded quota: compute, '
             'requested: requests.cpu=500m, used: requests.cpu=3800m, limited: requests.cpu=4')


def _outcome(*entities, edges=()):
    g = OntologyGraph()
    for e in entities:
        g.add_entity(e)
    for src, dst, rel in edges:
        g.add_edge(Edge(src, dst, rel))
    return diagnose(extract_signals(g))


def test_an_exhausted_quota_is_the_cause():
    o = _outcome(K8sEvent(uid="e", name="e", namespace="staging", reason="FailedCreate", message=QUOTA_MSG,
                          event_type="Warning", involved_kind="ReplicaSet", involved_name="w-7d8f9c6b5"))
    assert [(d.rule, d.cause_location) for d in o.diagnoses] == [("quota.exceeded", "ResourceQuota/staging/compute")]


def test_no_quota_message_no_quota_diagnosis():                                    # control
    o = _outcome(K8sEvent(uid="e", name="e", namespace="staging", reason="FailedCreate",
                          message="Error creating: pods is forbidden: some admission webhook said no",
                          event_type="Warning", involved_kind="ReplicaSet", involved_name="w-1"))
    assert not any(d.rule == "quota.exceeded" for d in o.diagnoses)


def _db_case(with_pool_timeout=True, with_exhaustion=True):
    dep = Deployment(uid="d", name="orders", namespace="prod", replicas=8, selector={"app": "orders"})
    pod = Pod(uid="p", name="orders-6d8f-x", namespace="prod", phase="Running", labels={"app": "orders"},
              container_statuses=[{"name": "c", "ready": False, "state": {"running": {}}}])
    ents, edges = [dep, pod], []
    if with_exhaustion:
        ents.append(PrometheusAlert(uid="a", name="PostgresConnectionsSaturated", namespace="prod",
                                    alert_name="PostgresConnectionsSaturated", severity="critical", state="firing",
                                    summary="postgres-primary connections at 100/100",
                                    alert_labels={"deployment": "orders", "db": "postgres-primary"}))
        edges.append(("d", "a", R.HAS_ALERT))
    if with_pool_timeout:
        ents.append(LokiLog(uid="l", name="l", namespace="prod", pod_name=pod.name, level="error",
                            log_line="pool timeout: could not acquire connection db=orders"))
        edges.append(("p", "l", R.HAS_LOG))
    return _outcome(*ents, edges=edges)


def test_connection_exhaustion_names_the_database_and_the_scale():
    d = _db_case().diagnoses[0]
    assert (d.rule, d.cause_location) == ("db.connections_exhausted", "outside:postgres-primary")
    assert "8 replicas" in d.cause


def test_exhaustion_without_a_failing_connect_from_the_workload_does_not_fire():   # control
    assert not any(d.rule == "db.connections_exhausted" for d in _db_case(with_pool_timeout=False).diagnoses)


def test_pool_timeouts_without_exhaustion_do_not_fire():                            # control
    assert not any(d.rule == "db.connections_exhausted" for d in _db_case(with_exhaustion=False).diagnoses)
