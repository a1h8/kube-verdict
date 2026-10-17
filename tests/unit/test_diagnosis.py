"""Typed diagnoses — docs/structured-diagnosis.md § 2 and § Step 2."""
from __future__ import annotations

from ontology.entities import Deployment, HelmRelease, K8sEvent, Pod
from ontology.graph import OntologyGraph
from rca.diagnosis import GENERIC_CAP, REPORT_MIN, RULES_VERSION, diagnose
from rca.signals import extract_signals


def _pod(name="api-6c8b7f9d4-x1", *, ready=False, state=None, last=None, restarts=0) -> Pod:
    return Pod(uid=f"pod-{name}", name=name, namespace="prod", phase="Running", restart_count=restarts,
               owner_ref_kind="ReplicaSet", owner_ref_name="api-6c8b7f9d4",
               container_statuses=[{"name": "api", "ready": ready, "restartCount": restarts,
                                    "state": state or {"running": {}}, "lastState": last or {}}])


def _event(message, reason="Failed", pod="api-6c8b7f9d4-x1") -> K8sEvent:
    return K8sEvent(uid=f"ev-{abs(hash(message))}", name="ev", namespace="prod", reason=reason, message=message,
                    event_type="Warning", involved_kind="Pod", involved_name=pod)


def _release(name="api", **drift) -> HelmRelease:
    r = HelmRelease(uid=f"rel-{name}", name=name, namespace="prod")
    for field, (declared, observed) in drift.items():
        r.annotations[f"drift.helm.{field}"] = (f"drift field={field.replace('_', '.')} declared='{declared}' "
                                                f"[values.yaml] observed='{observed}' [helm-deployed]")
    return r


def _outcome(*entities):
    g = OntologyGraph()
    for e in entities:
        g.add_entity(e)
    return diagnose(extract_signals(g))


def test_missing_dependency_is_located_on_the_absent_object():
    o = _outcome(_pod(state={"waiting": {"reason": "CreateContainerConfigError"}}),
                 _event('Error: configmap "api-config" not found'))
    d = o.diagnoses[0]
    assert (o.outcome, d.family, d.cause_location) == ("cause", "config-missing", "absent:ConfigMap/prod/api-config")
    assert d.score > REPORT_MIN          # strengthened by the container state
    assert d.rule_version == RULES_VERSION and d.signal_ids


def test_oom_with_a_memory_drift_names_the_drift():
    o = _outcome(_pod(last={"terminated": {"reason": "OOMKilled"}}, restarts=2,
                      state={"waiting": {"reason": "CrashLoopBackOff"}}),
                 _release(resources_limits_memory=("512Mi", "128Mi")))
    top = o.diagnoses[0]
    assert top.rule == "oom.memory_drift" and top.cause_location == "HelmRelease/prod/api"
    assert "512Mi" in top.cause and "128Mi" in top.cause


def test_a_drift_on_a_healthy_workload_is_not_reported():
    """The h016 decoy: a drift with nothing of that release failing stays generic."""
    o = _outcome(_release(name="catalog", spec_replicas=("8", "1")),
                 Pod(uid="p", name="catalog-7b5c4d8f9-q2", namespace="prod", phase="Running",
                     container_statuses=[{"name": "c", "ready": True, "state": {"running": {}}}]))
    assert o.outcome == "no_incident" and not o.diagnoses


def test_generic_rules_never_reach_the_report_threshold():
    assert GENERIC_CAP < REPORT_MIN
    o = _outcome(_pod(state={"waiting": {"reason": "CrashLoopBackOff"}}, restarts=4))
    assert o.outcome == "insufficient_evidence"


def test_a_healthy_rollout_is_no_incident():
    """h026: a pod starting cleanly in a progressing, available rollout, with a probe warning."""
    dep = Deployment(uid="d", name="api", namespace="prod", replicas=3, ready_replicas=3, available_replicas=3,
                     conditions=[{"type": "Progressing", "status": "True", "reason": "ReplicaSetUpdated"},
                                 {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"}])
    o = _outcome(dep, _pod(), _event("Readiness probe failed: connection refused", "Unhealthy"))
    assert o.outcome == "no_incident"


def test_diagnosis_is_deterministic():
    entities = (_pod(state={"waiting": {"reason": "CreateContainerConfigError"}}),
                _event('Error: configmap "a" not found'), _event('Error: secret "b" not found'))
    first, second = _outcome(*entities), _outcome(*reversed(entities))
    assert [(d.rule, d.cause_location, d.score) for d in first.diagnoses] == \
           [(d.rule, d.cause_location, d.score) for d in second.diagnoses]


def _init_pod(init_state, init_last=None):
    p = _pod(state={"waiting": {"reason": "PodInitializing"}})
    p.init_container_statuses = [{"name": "db-migrate", "ready": False, "restartCount": 3,
                                  "state": init_state, "lastState": init_last or {}}]
    return p


def test_a_failing_init_container_is_the_cause():
    o = _outcome(_init_pod({"waiting": {"reason": "CrashLoopBackOff"}}, {"terminated": {"exitCode": 1}}),
                 _event("Error: container db-migrate in pod api-6c8b7f9d4-x1 exited with code 1: migration failed"))
    d = o.diagnoses[0]
    assert (d.rule, d.family, d.cause_location) == ("init.failed", "init-container", "Pod/prod/api-6c8b7f9d4-x1")
    assert "db-migrate" in d.cause and d.score > 0.5          # strengthened by the exit event


def test_an_init_container_that_completed_is_not_a_cause():
    o = _outcome(_init_pod({"terminated": {"exitCode": 0, "reason": "Completed"}}))
    assert all(d.rule != "init.failed" for d in o.diagnoses)


_DENIED = 'pods is forbidden: User "system:serviceaccount:prod:exporter" cannot list resource "pods" in API group ""'


def test_an_rbac_denial_names_the_serviceaccount():
    p = _pod(ready=True)
    p.annotations["missing.rbac.exporter"] = "ServiceAccount 'exporter' exists but has no (Cluster)RoleBinding"
    o = _outcome(p, _event(_DENIED, "FailedList"))
    d = o.diagnoses[0]
    assert (d.rule, d.family, d.cause_location) == ("rbac", "rbac", "ServiceAccount/prod/exporter")
    assert "RoleBinding" in d.cause and d.score > 0.5             # strengthened by rbac.unbound


def test_an_unbound_serviceaccount_without_denial_is_no_fault():
    p = _pod(ready=True)
    p.annotations["missing.rbac.exporter"] = "ServiceAccount 'exporter' exists but has no (Cluster)RoleBinding"
    o = _outcome(p)
    assert o.outcome == "no_incident" and not o.diagnoses


def _hpa_event(message, reason, event_type="Warning"):
    return K8sEvent(uid=f"ev-{reason}", name="ev", namespace="prod", reason=reason, message=message,
                    event_type=event_type, involved_kind="HorizontalPodAutoscaler", involved_name="api")


def test_an_hpa_without_metrics_points_to_the_metrics_api():
    o = _outcome(_hpa_event("failed to get cpu utilization: unable to get metrics for resource cpu: "
                            "no metrics returned from resource metrics API", "FailedGetResourceMetric"),
                 _hpa_event("invalid metrics (1 invalid out of 1)", "FailedComputeMetricsReplicas"))
    d = o.diagnoses[0]
    assert (d.rule, d.family) == ("hpa.metrics", "hpa")
    assert d.cause_location == "outside:APIService/v1beta1.metrics.k8s.io" and "HPA api" in d.cause


def test_a_normal_hpa_rescale_is_no_cause():
    o = _outcome(_hpa_event("New size: 4; reason: cpu resource utilization above target", "SuccessfulRescale",
                            event_type="Normal"))
    assert o.outcome == "no_incident"
