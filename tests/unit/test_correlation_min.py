"""rca/correlation.py (M4-min), on fixtures written for this test, not S2's."""
from __future__ import annotations

from ontology.discovery import APIResourceInfo
from ontology.dynamic_entity import GenericEntity
from ontology.entities import ConfigMap, Deployment, K8sEvent, Namespace, Pod
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType as R
from rca.correlation import correlate, quantity


def _g(kind, plural, name, spec=None, status=None, owner=None, group=""):
    meta = {"name": name, "namespace": "ns", "uid": f"{kind}-{name}"}
    if owner:
        meta["ownerReferences"] = [{"kind": owner[0], "name": owner[1]}]
    return GenericEntity.from_api_object({"metadata": meta, "spec": spec or {}, "status": status or {}},
                                         APIResourceInfo(group=group, version="v1", kind=kind, plural=plural,
                                                         namespaced=True, verbs=["list"]))


def _event(uid, kind, name, msg):
    return K8sEvent(uid=uid, name=f"{name}.1", namespace="ns", reason="FailedCreate", event_type="Warning",
                    involved_kind=kind, involved_name=name, message=msg)


def test_quantities():
    assert quantity("512Mi") == 512 * 2**20 and quantity("250m") == 0.25 and quantity(40) == 40.0


def _quota_case(used_pods: str) -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns", name="ns"))
    q = _g("ResourceQuota", "resourcequotas", "q", spec={"hard": {"pods": "4"}},
           status={"hard": {"pods": "4"}, "used": {"pods": used_pods}})
    cj = _g("CronJob", "cronjobs", "batch", group="batch")
    web = Deployment(uid="Deployment-web", name="web", namespace="ns")   # typed, as the collector builds it
    ev = _event("ev", "ReplicaSet", "web-1", "exceeded quota: q, requested: pods=1, used: pods=4, limited: pods=4")
    for e in (q, cj, web, ev):
        g.add_entity(e)
    for i in range(3):
        p = Pod(uid=f"pj{i}", name=f"batch-{i}", namespace="ns", phase="Running")
        g.add_entity(p)
        g.add_edge(Edge(cj.uid, p.uid, R.OWNS))
    p = Pod(uid="pw", name="web-0", namespace="ns", phase="Running")
    g.add_entity(p)
    g.add_edge(Edge(web.uid, p.uid, R.OWNS))
    g.add_edge(Edge(q.uid, ev.uid, R.QUOTA_BLOCKS, {"method": "admission refusal (quota)", "evidence": "pods"}))
    return g


def test_a_full_quota_is_attributed_to_the_owner_holding_most_of_it():
    chains = correlate(_quota_case("4"), seeds=None)
    assert [c.cause for c in chains] == ["CronJob/ns/batch"]
    assert "75%" in chains[0].mechanism


def test_a_quota_below_its_limit_gives_no_chain():
    assert correlate(_quota_case("3")) == []


def _lr_case(oom: bool) -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns", name="ns"))
    lr = _g("LimitRange", "limitranges", "defaults")
    last = {"terminated": {"reason": "OOMKilled"}} if oom else {"terminated": {"reason": "Completed"}}
    p = Pod(uid="p", name="w-1", namespace="ns", phase="Running",
            container_statuses=[{"name": "w", "ready": False, "restartCount": 3}],
            raw={"status": {"container_statuses": [{"name": "w", "ready": False, "last_state": last}]}})
    g.add_entity(lr)
    g.add_entity(p)
    g.add_edge(Edge(lr.uid, p.uid, R.LIMITS, {"method": "default injected (annotation) (value)", "evidence": "256Mi"}))
    return g


def test_an_injected_default_on_an_oomkilled_container_is_a_cause():
    assert [c.cause for c in correlate(_lr_case(True))] == ["LimitRange/ns/defaults"]


def test_an_injected_default_without_oom_is_not():
    assert correlate(_lr_case(False)) == []


def _cm_case(written: str) -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Namespace(uid="ns", name="ns"))
    cm = ConfigMap(uid="cm", name="env", namespace="ns",
                   raw={"metadata": {"managed_fields": [{"manager": "helm", "time": written}]}})
    p = Pod(uid="p", name="api-1", namespace="ns", phase="Pending",
            raw={"metadata": {"creation_timestamp": "2026-01-01T10:00:00Z"}})
    g.add_entity(cm)
    g.add_entity(p)
    g.add_edge(Edge(cm.uid, p.uid, R.CONFIGURES, {"method": "envFrom", "evidence": "reads env"}))
    return g


def test_a_configmap_written_before_the_failing_pod_was_created_is_a_cause():
    assert [c.cause for c in correlate(_cm_case("2026-01-01T09:00:00Z"))] == ["ConfigMap/ns/env"]


def test_a_configmap_written_after_it_is_not():
    assert correlate(_cm_case("2026-01-01T11:00:00Z")) == []


def test_completed_pods_do_not_count_towards_the_quota():
    """A pods quota counts non-terminal pods (Kubernetes documentation, ResourceQuota): five Succeeded pods of
    web must not make it the main consumer."""
    g = _quota_case("4")
    web = next(e for e in g.entities() if e.uid == "Deployment-web")
    for i in range(5):
        p = Pod(uid=f"done{i}", name=f"web-done-{i}", namespace="ns", phase="Succeeded")
        g.add_entity(p)
        g.add_edge(Edge(web.uid, p.uid, R.OWNS))
    assert [c.cause for c in correlate(g)] == ["CronJob/ns/batch"]


def test_d_reports_the_chain_first_and_keeps_the_rules_after_it():
    from rca.diagnosis import diagnose
    from rca.signals import extract_signals
    g = _lr_case(True)
    ss = extract_signals(g)
    with_graph = diagnose(ss, g)
    assert with_graph.outcome == "cause"
    assert with_graph.diagnoses[0].family == "correlation"
    assert with_graph.root_cause().startswith("LimitRange/ns/defaults")
    without = diagnose(ss)                          # no graph: the rules alone, as before
    assert all(d.family != "correlation" for d in without.diagnoses)


def test_a_chain_outranks_a_rule_and_names_the_cause(monkeypatch):
    """With a rule firing too (here a rule that always reports a high-score cause), the chain comes first and the
    report's root cause is the chain's; the rule's diagnosis stays listed after it."""
    import rca.diagnosis as dg
    from rca.signals import extract_signals
    rule = lambda ss: [dg.Diagnosis(family="oom", cause="The container is OOMKilled.",  # noqa: E731
                                    cause_location="Pod/ns/w-1", rule="oom.limit", score=0.9)]
    monkeypatch.setattr(dg, "RULES", [rule])
    g = _lr_case(True)
    out = dg.diagnose(extract_signals(g), g)
    assert [d.family for d in out.diagnoses] == ["correlation", "oom"]
    assert out.root_cause().startswith("LimitRange/ns/defaults") and "OOMKilled." not in out.root_cause()
