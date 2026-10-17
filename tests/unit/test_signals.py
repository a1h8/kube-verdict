"""Typed signals — docs/structured-diagnosis.md § 1 (findings S1–S3)."""
from __future__ import annotations

from ontology.entities import Deployment, K8sEvent, LokiLog, Pod
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType
from rca.signals import extract_signals


def _pod(name="api-1", *, phase="Running", ready=True, state=None, last=None, restarts=0) -> Pod:
    cs = {"name": "api", "ready": ready, "restartCount": restarts,
          "state": state or {"running": {}}, "lastState": last or {}}
    return Pod(uid=f"pod-{name}", name=name, namespace="prod", phase=phase,
               restart_count=restarts, container_statuses=[cs])


def _event(message: str, reason="Failed", pod="api-1") -> K8sEvent:
    return K8sEvent(uid=f"ev-{abs(hash(message))}", name="ev", namespace="prod", reason=reason,
                    message=message, event_type="Warning", involved_kind="Pod", involved_name=pod)


def _graph(*entities, edges=()) -> OntologyGraph:
    g = OntologyGraph()
    for e in entities:
        g.add_entity(e)
    for e in edges:
        g.add_edge(e)
    return g


def _kinds(*entities, **kw) -> set[str]:
    return extract_signals(_graph(*entities, **kw)).kinds()


def test_container_states_become_signals():
    crash = _pod(ready=False, state={"waiting": {"reason": "CrashLoopBackOff"}},
                 last={"terminated": {"reason": "OOMKilled"}}, restarts=3)
    assert {"container.crashloop", "container.oom_killed", "pod.not_ready"} <= _kinds(crash)


def test_a_missing_dependency_is_read_from_the_event():
    out = extract_signals(_graph(_pod(ready=False), _event('Error: secret "db-creds" not found')))
    sig = out.of("dependency.missing")[0]
    assert sig.attr("kind") == "secret" and sig.attr("name") == "db-creds"
    assert out.subject(sig) == "Pod/prod/api-1"


def test_lifecycle_warning_about_a_pod_healthy_now_is_history():           # S2
    kinds = _kinds(_pod(restarts=1), _event("Back-off restarting failed container api", "BackOff"))
    assert "container.crashloop" not in kinds and {"event.past", "restart.past"} <= kinds


def test_an_rbac_denial_on_a_running_pod_stays_current():                  # S2, h005
    msg = 'pods is forbidden: User "system:serviceaccount:prod:sa" cannot list resource "pods"'
    assert "rbac.denied" in _kinds(_pod(), _event(msg, "FailedList"))


def test_log_lines_are_attached_to_their_pod():
    pod = _pod(ready=False)
    log = LokiLog(uid="log-1", name="log-1", namespace="prod", level="error", pod_name="api-1",
                  log_line='err="x509: certificate has expired or is not yet valid"')
    out = extract_signals(_graph(pod, log, edges=[Edge(pod.uid, log.uid, RelationshipType.HAS_LOG)]))
    sig = out.of("tls.cert_expired")[0]
    assert out.subject(sig) == "Pod/prod/api-1" and sig.source == "loki" and sig.plan == "telemetry"


def test_unknown_text_is_counted_not_guessed():
    out = extract_signals(_graph(_pod(ready=False), _event("something nobody has seen before")))
    assert out.unclassified["k8s_event"] == 1


def test_rollout_conditions():
    dep = Deployment(uid="d", name="api", namespace="prod",
                     conditions=[{"type": "Progressing", "status": "True", "reason": "ReplicaSetUpdated"},
                                 {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"}])
    stalled = Deployment(uid="s", name="web", namespace="prod",
                         conditions=[{"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}])
    assert {"rollout.progressing", "rollout.stalled"} <= _kinds(dep, stalled)


def test_extraction_is_deterministic():
    entities = (_pod(ready=False, state={"waiting": {"reason": "CrashLoopBackOff"}}),
                _event('Error: configmap "cfg" not found'))
    assert extract_signals(_graph(*entities)).signals == extract_signals(_graph(*reversed(entities))).signals


def test_the_envelope_separates_authored_from_observed():
    """docs/structured-diagnosis.md § 1: authored ≠ observed ≠ derived."""
    out = extract_signals(_graph(_pod(ready=False), _event('Error: configmap "cfg" not found')))
    sig = out.of("dependency.missing")[0]
    assert dict(sig.authored) == {"involved": "Pod/prod/api-1", "reason": "Failed"}   # kubelet wrote it
    assert dict(sig.observed) == {"kind": "configmap", "name": "cfg"}                  # we read it
    assert sig.payload_ref.startswith("ev-") and sig.extractor_version == "signals/1"


def test_attribution_is_a_derived_edge_with_its_method():
    out = extract_signals(_graph(_pod(ready=False), _event('Error: configmap "cfg" not found')))
    sig = out.of("dependency.missing")[0]
    (edge,) = [e for e in out.edges if e.signal_id == sig.signal_id]
    assert (edge.entity, edge.method, edge.confidence) == ("Pod/prod/api-1", "event.involvedObject", 1.0)
    assert edge.correlator_version == "attribution/1"
