"""
Unit tests for RemediationEngine — rule-based weighted fallback.
"""
from ontology.entities import (
    DaemonSet, Deployment, K8sEvent, Pod, StatefulSet,
)
from ontology.graph import OntologyGraph
from rca.remediation_engine import (
    RemediationEngine,
    _OOMKillRule,
    _CrashLoopDBRule,
    _ImagePullRule,
    _MissingConfigRule,
    _PendingSchedulingRule,
    _HelmDriftRule,
    _DegradedDeploymentRule,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _graph(*entities) -> OntologyGraph:
    g = OntologyGraph()
    for e in entities:
        g.add_entity(e)
    return g


def _oom_pod(name: str = "worker-0", ns: str = "prod") -> Pod:
    p = Pod(uid=f"p-{name}", name=name, namespace=ns, phase="Error")
    p.container_statuses = [
        {"lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}
    ]
    return p


def _crashloop_pod(name: str = "api-0", ns: str = "prod", restarts: int = 8) -> Pod:
    return Pod(uid=f"p-{name}", name=name, namespace=ns,
               phase="CrashLoopBackOff", restart_count=restarts)


def _warning_event(
    involved: str, reason: str, message: str, ns: str = "prod"
) -> K8sEvent:
    return K8sEvent(
        uid=f"ev-{involved}-{reason}",
        name=f"ev-{involved}",
        namespace=ns,
        event_type="Warning",
        involved_name=involved,
        reason=reason,
        message=message,
    )


# ─────────────────────────────────────────────────────────────────────────────
# OOMKill rule
# ─────────────────────────────────────────────────────────────────────────────

class TestOOMKillRule:
    def test_matches_oom_pod(self):
        pod = _oom_pod()
        assert _OOMKillRule().match(pod, _graph(pod)) is True

    def test_no_match_running_pod(self):
        pod = Pod(uid="p1", name="ok", phase="Running", container_statuses=[])
        assert _OOMKillRule().match(pod, _graph(pod)) is False

    def test_no_match_non_pod(self):
        dep = Deployment(uid="d1", name="d", replicas=2, ready_replicas=0)
        assert _OOMKillRule().match(dep, _graph(dep)) is False

    def test_weight_base(self):
        assert _OOMKillRule().base_weight >= 0.85

    def test_memory_drift_boost(self):
        pod = _oom_pod()
        pod.annotations["drift.resources.limits.memory"] = "declared=512Mi observed=50Mi"
        boosts = _OOMKillRule().evidence_boosts(pod, _graph(pod))
        assert any("drift" in desc for desc, _ in boosts)
        total_boost = sum(b for _, b in boosts)
        assert total_boost >= 0.05

    def test_commands_include_describe(self):
        pod = _oom_pod()
        cmds = _OOMKillRule().commands(pod, _graph(pod))
        assert any("describe pod" in c for c in cmds)


# ─────────────────────────────────────────────────────────────────────────────
# CrashLoop / DB rule
# ─────────────────────────────────────────────────────────────────────────────

class TestCrashLoopDBRule:
    def test_matches_crashloop_with_event(self):
        pod = _crashloop_pod(restarts=3)
        ev  = _warning_event(pod.name, "BackOff", "connection refused to db:5432")
        g   = _graph(pod, ev)
        assert _CrashLoopDBRule().match(pod, g) is True

    def test_matches_high_restart_no_oom(self):
        pod = _crashloop_pod(restarts=10)
        assert _CrashLoopDBRule().match(pod, _graph(pod)) is True

    def test_no_match_low_restarts_no_event(self):
        pod = _crashloop_pod(restarts=1)
        assert _CrashLoopDBRule().match(pod, _graph(pod)) is False

    def test_weight_boost_on_high_restarts(self):
        pod = _crashloop_pod(restarts=15)
        boosts = _CrashLoopDBRule().evidence_boosts(pod, _graph(pod))
        assert any(b >= 0.05 for _, b in boosts)

    def test_commands_include_logs(self):
        pod = _crashloop_pod()
        cmds = _CrashLoopDBRule().commands(pod, _graph(pod))
        assert any("logs" in c for c in cmds)


# ─────────────────────────────────────────────────────────────────────────────
# ImagePull rule
# ─────────────────────────────────────────────────────────────────────────────

class TestImagePullRule:
    def test_matches_imagepullbackoff_phase(self):
        pod = Pod(uid="p1", name="ml-0", phase="ImagePullBackOff")
        assert _ImagePullRule().match(pod, _graph(pod)) is True

    def test_matches_via_event(self):
        pod = Pod(uid="p1", name="ml-0", phase="Pending")
        ev  = _warning_event(pod.name, "Failed", "Failed to pull image: not found")
        g   = _graph(pod, ev)
        assert _ImagePullRule().match(pod, g) is True

    def test_no_match_running(self):
        pod = Pod(uid="p1", name="ml-0", phase="Running")
        assert _ImagePullRule().match(pod, _graph(pod)) is False

    def test_image_drift_boost(self):
        pod = Pod(uid="p1", name="ml-0", phase="ImagePullBackOff")
        pod.annotations["drift.image.tag"] = "declared=nginx:1.25 observed=broken:tag"
        boosts = _ImagePullRule().evidence_boosts(pod, _graph(pod))
        assert any(b >= 0.05 for _, b in boosts)


# ─────────────────────────────────────────────────────────────────────────────
# Missing ConfigMap rule
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingConfigRule:
    def test_matches_via_event(self):
        pod = Pod(uid="p1", name="notif-0", phase="Pending")
        ev  = _warning_event(pod.name, "Failed",
                             "configmap notification-config not found")
        g   = _graph(pod, ev)
        assert _MissingConfigRule().match(pod, g) is True

    def test_no_match_no_event(self):
        pod = Pod(uid="p1", name="notif-0", phase="Pending")
        assert _MissingConfigRule().match(pod, _graph(pod)) is False

    def test_sibling_boost(self):
        pod1 = Pod(uid="p1", name="n-1", namespace="prod",
                   phase="Pending", owner_ref_name="notif")
        pod2 = Pod(uid="p2", name="n-2", namespace="prod",
                   phase="Pending", owner_ref_name="notif")
        ev = _warning_event("n-1", "Failed", "secret not found")
        g = _graph(pod1, pod2, ev)
        boosts = _MissingConfigRule().evidence_boosts(pod1, g)
        assert any("sibling" in desc for desc, _ in boosts)


# ─────────────────────────────────────────────────────────────────────────────
# Pending / unschedulable rule
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingSchedulingRule:
    def test_matches_pending_with_event(self):
        pod = Pod(uid="p1", name="gpu-0", phase="Pending")
        ev  = _warning_event(pod.name, "FailedScheduling",
                             "0/1 nodes available: Unschedulable nodeSelector")
        g   = _graph(pod, ev)
        assert _PendingSchedulingRule().match(pod, g) is True

    def test_matches_pending_no_event(self):
        pod = Pod(uid="p1", name="gpu-0", phase="Pending")
        assert _PendingSchedulingRule().match(pod, _graph(pod)) is True

    def test_no_match_running(self):
        pod = Pod(uid="p1", name="ok", phase="Running")
        assert _PendingSchedulingRule().match(pod, _graph(pod)) is False

    def test_unschedulable_event_boost(self):
        pod = Pod(uid="p1", name="gpu-0", phase="Pending")
        ev  = _warning_event(pod.name, "Unschedulable", "no nodes")
        g   = _graph(pod, ev)
        boosts = _PendingSchedulingRule().evidence_boosts(pod, g)
        assert any(b >= 0.05 for _, b in boosts)


# ─────────────────────────────────────────────────────────────────────────────
# Helm drift rule
# ─────────────────────────────────────────────────────────────────────────────

class TestHelmDriftRule:
    def test_matches_entity_with_drift(self):
        pod = Pod(uid="p1", name="api-0")
        pod.annotations["drift.resources.limits.memory"] = "declared=256Mi observed=50Mi"
        assert _HelmDriftRule().match(pod, _graph(pod)) is True

    def test_no_match_no_drift(self):
        pod = Pod(uid="p1", name="api-0")
        assert _HelmDriftRule().match(pod, _graph(pod)) is False

    def test_multiple_drifts_higher_boost(self):
        pod = Pod(uid="p1", name="api-0")
        pod.annotations["drift.memory"] = "x"
        pod.annotations["drift.image"]  = "y"
        pod.annotations["drift.cpu"]    = "z"
        boosts = _HelmDriftRule().evidence_boosts(pod, _graph(pod))
        assert any(b >= 0.05 for _, b in boosts)

    def test_commands_include_helm_upgrade(self):
        pod = Pod(uid="p1", name="analytics-worker")
        pod.annotations["drift.resources.limits.memory"] = "x"
        cmds = _HelmDriftRule().commands(pod, _graph(pod))
        assert any("helm upgrade" in c for c in cmds)


# ─────────────────────────────────────────────────────────────────────────────
# Degraded deployment rule
# ─────────────────────────────────────────────────────────────────────────────

class TestDegradedDeploymentRule:
    def test_matches_degraded_deployment(self):
        dep = Deployment(uid="d1", name="api", replicas=3, ready_replicas=0)
        assert _DegradedDeploymentRule().match(dep, _graph(dep)) is True

    def test_no_match_healthy(self):
        dep = Deployment(uid="d1", name="api", replicas=3, ready_replicas=3)
        assert _DegradedDeploymentRule().match(dep, _graph(dep)) is False

    def test_matches_degraded_statefulset(self):
        sts = StatefulSet(uid="s1", name="db", replicas=3, ready_replicas=1)
        assert _DegradedDeploymentRule().match(sts, _graph(sts)) is True

    def test_zero_ready_boost(self):
        dep = Deployment(uid="d1", name="api", replicas=3, ready_replicas=0)
        boosts = _DegradedDeploymentRule().evidence_boosts(dep, _graph(dep))
        assert any(b >= 0.08 for _, b in boosts)


# ─────────────────────────────────────────────────────────────────────────────
# Engine integration
# ─────────────────────────────────────────────────────────────────────────────

class TestRemediationEngine:
    def test_empty_graph_returns_empty(self):
        assert RemediationEngine().score(OntologyGraph()) == []

    def test_returns_sorted_by_weight(self):
        pod = _oom_pod()
        pod.annotations["drift.resources.limits.memory"] = "x"
        g = _graph(pod)
        results = RemediationEngine().score(g)
        weights = [h.weight for h in results]
        assert weights == sorted(weights, reverse=True)

    def test_oom_hypothesis_present(self):
        pod = _oom_pod()
        g = _graph(pod)
        results = RemediationEngine().score(g)
        rule_ids = [h.rule_id for h in results]
        assert "oom_kill" in rule_ids

    def test_deduplication(self):
        """Same rule + same entity should not produce duplicates."""
        pod = _oom_pod()
        g = _graph(pod)
        results = RemediationEngine().score(g)
        oom_hits = [h for h in results if h.rule_id == "oom_kill"]
        assert len(oom_hits) == 1

    def test_weight_capped_at_1(self):
        pod = _oom_pod()
        pod.annotations["drift.resources.limits.memory"] = "x"
        pod.annotations["signal.memory_anomaly"] = "severity=critical"
        g = _graph(pod)
        results = RemediationEngine().score(g)
        for h in results:
            assert h.weight <= 1.0

    def test_multiple_scenarios(self):
        oom  = _oom_pod(name="worker-0")
        img  = Pod(uid="p2", name="ml-0", phase="ImagePullBackOff")
        pend = Pod(uid="p3", name="gpu-0", phase="Pending")
        ev   = _warning_event("gpu-0", "Unschedulable", "no nodes available")
        g    = _graph(oom, img, pend, ev)
        results = RemediationEngine().score(g)
        rule_ids = {h.rule_id for h in results}
        assert "oom_kill" in rule_ids
        assert "image_pull_backoff" in rule_ids
        assert "pending_unschedulable" in rule_ids

    def test_custom_rules_respected(self):
        """Engine accepts injected rule list."""
        pod = _oom_pod()
        g = _graph(pod)
        results = RemediationEngine(rules=[_OOMKillRule()]).score(g)
        assert all(h.rule_id == "oom_kill" for h in results)

    def test_hypothesis_has_commands(self):
        pod = _oom_pod()
        results = RemediationEngine().score(_graph(pod))
        oom = next(h for h in results if h.rule_id == "oom_kill")
        assert len(oom.commands) > 0

    def test_affected_is_fqn(self):
        pod = _oom_pod(name="worker", ns="prod")
        results = RemediationEngine().score(_graph(pod))
        oom = next(h for h in results if h.rule_id == "oom_kill")
        assert "worker" in oom.affected


# ─────────────────────────────────────────────────────────────────────────────
# OOMKill — extended boundary tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOOMKillRuleExtended:
    def test_match_oom_in_current_state(self):
        pod = Pod(uid="p1", name="w", phase="Error")
        pod.container_statuses = [
            {"state": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}
        ]
        assert _OOMKillRule().match(pod, _graph(pod)) is True

    def test_no_match_empty_container_statuses(self):
        pod = Pod(uid="p1", name="w", phase="Error", container_statuses=[])
        assert _OOMKillRule().match(pod, _graph(pod)) is False

    def test_no_match_non_oom_exit(self):
        pod = Pod(uid="p1", name="w", phase="Error")
        pod.container_statuses = [
            {"lastState": {"terminated": {"reason": "Error", "exitCode": 1}}}
        ]
        assert _OOMKillRule().match(pod, _graph(pod)) is False

    def test_all_boosts_accumulate(self):
        pod = _oom_pod()
        pod.annotations["drift.resources.limits.memory"] = "declared=512Mi observed=50Mi"
        pod.annotations["signal.memory_anomaly"] = "severity=critical anomaly=true"
        ev = _warning_event(pod.name, "OOM", "OOM event for pod")
        g = _graph(pod, ev)
        boosts = _OOMKillRule().evidence_boosts(pod, g)
        total = sum(b for _, b in boosts)
        assert total >= 0.10  # drift(0.07) + signal(0.03) + event(0.02)

    def test_commands_include_top_pod(self):
        pod = _oom_pod()
        cmds = _OOMKillRule().commands(pod, _graph(pod))
        assert any("top pod" in c for c in cmds)

    def test_commands_include_patch_when_owner(self):
        pod = _oom_pod(name="worker-abc-xyz")
        pod.owner_ref_kind = "ReplicaSet"
        dep = Deployment(uid="d1", name="worker", namespace="prod", replicas=1, ready_replicas=0)
        g = _graph(pod, dep)
        cmds = _OOMKillRule().commands(pod, g)
        assert any("patch" in c and "memory" in c for c in cmds)

    def test_hypothesis_has_explanation(self):
        pod = _oom_pod()
        hyp = RemediationEngine(rules=[_OOMKillRule()]).score(_graph(pod))[0]
        assert hyp.explanation != ""


# ─────────────────────────────────────────────────────────────────────────────
# CrashLoop — extended boundary tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCrashLoopDBRuleExtended:
    def test_no_match_restarts_4_no_event(self):
        pod = _crashloop_pod(restarts=4)
        assert _CrashLoopDBRule().match(pod, _graph(pod)) is False

    def test_match_exactly_restarts_5(self):
        pod = _crashloop_pod(restarts=5)
        assert _CrashLoopDBRule().match(pod, _graph(pod)) is True

    def test_no_match_oom_pod_with_high_restarts(self):
        pod = _oom_pod()
        pod.restart_count = 10
        assert _CrashLoopDBRule().match(pod, _graph(pod)) is False

    def test_match_econnrefused_event(self):
        pod = _crashloop_pod(restarts=2)
        ev = _warning_event(pod.name, "BackOff", "econnrefused on port 5432")
        assert _CrashLoopDBRule().match(pod, _graph(pod, ev)) is True

    def test_match_connection_timeout_event(self):
        pod = _crashloop_pod(restarts=2)
        ev = _warning_event(pod.name, "BackOff", "connection timeout reaching db:3306")
        assert _CrashLoopDBRule().match(pod, _graph(pod, ev)) is True

    def test_match_no_route_to_host(self):
        pod = _crashloop_pod(restarts=2)
        ev = _warning_event(pod.name, "BackOff", "dial tcp: no route to host")
        assert _CrashLoopDBRule().match(pod, _graph(pod, ev)) is True

    def test_boost_restarts_5_to_9(self):
        pod = _crashloop_pod(restarts=7)
        boosts = _CrashLoopDBRule().evidence_boosts(pod, _graph(pod))
        vals = [b for _, b in boosts]
        assert any(b == 0.03 for b in vals)

    def test_boost_restarts_10_plus(self):
        pod = _crashloop_pod(restarts=10)
        boosts = _CrashLoopDBRule().evidence_boosts(pod, _graph(pod))
        vals = [b for _, b in boosts]
        assert any(b >= 0.05 for b in vals)

    def test_commands_include_get_endpoints(self):
        pod = _crashloop_pod()
        cmds = _CrashLoopDBRule().commands(pod, _graph(pod))
        assert any("endpoints" in c for c in cmds)


# ─────────────────────────────────────────────────────────────────────────────
# ImagePull — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestImagePullRuleExtended:
    def test_matches_errimagepull_event(self):
        pod = Pod(uid="p1", name="ml-0", phase="Pending")
        ev = _warning_event(pod.name, "ErrImagePull", "failed to pull image")
        assert _ImagePullRule().match(pod, _graph(pod, ev)) is True

    def test_matches_backoff_event_with_pull_in_message(self):
        pod = Pod(uid="p1", name="ml-0", phase="Pending")
        ev = _warning_event(pod.name, "BackOff", "Back-off pulling image nginx:broken")
        assert _ImagePullRule().match(pod, _graph(pod, ev)) is True

    def test_no_match_backoff_event_unrelated_message(self):
        pod = Pod(uid="p1", name="ml-0", phase="Pending")
        ev = _warning_event(pod.name, "BackOff", "Back-off restarting failed container")
        assert _ImagePullRule().match(pod, _graph(pod, ev)) is False

    def test_commands_include_set_image(self):
        pod = Pod(uid="p1", name="ml-abc-xyz", namespace="prod", phase="ImagePullBackOff")
        pod.owner_ref_kind = "ReplicaSet"
        dep = Deployment(uid="d1", name="ml", namespace="prod", replicas=1, ready_replicas=0)
        g = _graph(pod, dep)
        cmds = _ImagePullRule().commands(pod, g)
        assert any("set image" in c for c in cmds)

    def test_commands_include_imagepullsecret_hint(self):
        pod = Pod(uid="p1", name="ml-0", phase="ImagePullBackOff")
        cmds = _ImagePullRule().commands(pod, _graph(pod))
        assert any("docker-registry" in c or "regcred" in c for c in cmds)


# ─────────────────────────────────────────────────────────────────────────────
# MissingConfig — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingConfigRuleExtended:
    def test_matches_createcontainerconfigerror(self):
        pod = Pod(uid="p1", name="n-0", phase="Pending")
        ev = _warning_event(pod.name, "Failed", "CreateContainerConfigError")
        assert _MissingConfigRule().match(pod, _graph(pod, ev)) is True

    def test_matches_secret_not_found(self):
        pod = Pod(uid="p1", name="n-0", phase="Pending")
        ev = _warning_event(pod.name, "Failed", "secret notification-secrets not found")
        assert _MissingConfigRule().match(pod, _graph(pod, ev)) is True

    def test_no_sibling_boost_when_no_siblings(self):
        pod = Pod(uid="p1", name="n-0", namespace="prod",
                  phase="Pending", owner_ref_name="notif")
        ev = _warning_event("n-0", "Failed", "configmap not found")
        g = _graph(pod, ev)
        boosts = _MissingConfigRule().evidence_boosts(pod, g)
        assert not any("sibling" in desc for desc, _ in boosts)

    def test_commands_include_get_secrets(self):
        pod = Pod(uid="p1", name="n-0", phase="Pending")
        ev = _warning_event(pod.name, "Failed", "secret not found")
        g = _graph(pod, ev)
        cmds = _MissingConfigRule().commands(pod, g)
        assert any("secret" in c for c in cmds)

    def test_commands_include_get_configmaps(self):
        pod = Pod(uid="p1", name="n-0", phase="Pending")
        ev = _warning_event(pod.name, "Failed", "configmap not found")
        g = _graph(pod, ev)
        cmds = _MissingConfigRule().commands(pod, g)
        assert any("configmap" in c for c in cmds)


# ─────────────────────────────────────────────────────────────────────────────
# PendingScheduling — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingSchedulingRuleExtended:
    def test_matches_insufficient_memory_event(self):
        pod = Pod(uid="p1", name="gpu-0", phase="Pending")
        ev = _warning_event(pod.name, "FailedScheduling", "insufficient memory on all nodes")
        assert _PendingSchedulingRule().match(pod, _graph(pod, ev)) is True

    def test_matches_taint_event(self):
        pod = Pod(uid="p1", name="gpu-0", phase="Pending")
        ev = _warning_event(pod.name, "FailedScheduling", "node has taint gpu=true:NoSchedule")
        assert _PendingSchedulingRule().match(pod, _graph(pod, ev)) is True

    def test_matches_toleration_event(self):
        pod = Pod(uid="p1", name="gpu-0", phase="Pending")
        ev = _warning_event(pod.name, "FailedScheduling", "missing toleration for taint")
        assert _PendingSchedulingRule().match(pod, _graph(pod, ev)) is True

    def test_boost_value_exactly_008(self):
        pod = Pod(uid="p1", name="gpu-0", phase="Pending")
        ev = _warning_event(pod.name, "Unschedulable", "no nodes")
        g = _graph(pod, ev)
        boosts = _PendingSchedulingRule().evidence_boosts(pod, g)
        boost_vals = [b for _, b in boosts if b == 0.08]
        assert len(boost_vals) == 1

    def test_commands_include_show_labels(self):
        pod = Pod(uid="p1", name="gpu-0", phase="Pending")
        cmds = _PendingSchedulingRule().commands(pod, _graph(pod))
        assert any("show-labels" in c for c in cmds)

    def test_commands_include_taints_check(self):
        pod = Pod(uid="p1", name="gpu-0", phase="Pending")
        cmds = _PendingSchedulingRule().commands(pod, _graph(pod))
        assert any("Taints" in c or "taints" in c.lower() for c in cmds)


# ─────────────────────────────────────────────────────────────────────────────
# HelmDrift — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestHelmDriftRuleExtended:
    def test_matches_deployment_with_drift(self):
        dep = Deployment(uid="d1", name="api", replicas=2, ready_replicas=2)
        dep.annotations["drift.resources.limits.cpu"] = "declared=500m observed=100m"
        assert _HelmDriftRule().match(dep, _graph(dep)) is True

    def test_matches_statefulset_with_drift(self):
        sts = StatefulSet(uid="s1", name="db", replicas=1, ready_replicas=1)
        sts.annotations["drift.image.tag"] = "x"
        assert _HelmDriftRule().match(sts, _graph(sts)) is True

    def test_boost_unhealthy_plus_drift(self):
        pod = _oom_pod()
        pod.annotations["drift.resources.limits.memory"] = "x"
        boosts = _HelmDriftRule().evidence_boosts(pod, _graph(pod))
        descs = [d for d, _ in boosts]
        assert any("unhealthy" in d or "drift" in d for d in descs)

    def test_single_drift_boost_lower_than_three(self):
        pod_one = Pod(uid="p1", name="a")
        pod_one.annotations["drift.memory"] = "x"
        pod_three = Pod(uid="p2", name="b")
        pod_three.annotations["drift.memory"] = "x"
        pod_three.annotations["drift.image"]  = "y"
        pod_three.annotations["drift.cpu"]    = "z"
        b1 = sum(b for _, b in _HelmDriftRule().evidence_boosts(pod_one, _graph(pod_one)))
        b3 = sum(b for _, b in _HelmDriftRule().evidence_boosts(pod_three, _graph(pod_three)))
        assert b3 > b1

    def test_commands_include_helm_diff(self):
        pod = Pod(uid="p1", name="analytics-worker")
        pod.annotations["drift.resources.limits.memory"] = "x"
        cmds = _HelmDriftRule().commands(pod, _graph(pod))
        assert any("helm diff" in c for c in cmds)

    def test_commands_include_kubectl_get(self):
        pod = Pod(uid="p1", name="api-worker")
        pod.annotations["drift.image"] = "x"
        cmds = _HelmDriftRule().commands(pod, _graph(pod))
        assert any("kubectl get" in c for c in cmds)


# ─────────────────────────────────────────────────────────────────────────────
# DegradedDeployment — extended
# ─────────────────────────────────────────────────────────────────────────────

class TestDegradedDeploymentRuleExtended:
    def test_matches_degraded_daemonset(self):
        ds = DaemonSet(uid="ds1", name="fluentd", desired=3, ready=1)
        assert _DegradedDeploymentRule().match(ds, _graph(ds)) is True

    def test_no_match_healthy_daemonset(self):
        ds = DaemonSet(uid="ds1", name="fluentd", desired=3, ready=3)
        assert _DegradedDeploymentRule().match(ds, _graph(ds)) is False

    def test_no_match_healthy_statefulset(self):
        sts = StatefulSet(uid="s1", name="db", replicas=3, ready_replicas=3)
        assert _DegradedDeploymentRule().match(sts, _graph(sts)) is False

    def test_partial_ready_boost(self):
        dep = Deployment(uid="d1", name="api", replicas=4, ready_replicas=1)
        boosts = _DegradedDeploymentRule().evidence_boosts(dep, _graph(dep))
        assert any(b >= 0.05 for _, b in boosts)

    def test_half_ready_no_sub50_boost(self):
        dep = Deployment(uid="d1", name="api", replicas=2, ready_replicas=1)
        boosts = _DegradedDeploymentRule().evidence_boosts(dep, _graph(dep))
        assert not any(b >= 0.05 for _, b in boosts)

    def test_commands_include_rollout_status(self):
        dep = Deployment(uid="d1", name="api", replicas=2, ready_replicas=0)
        cmds = _DegradedDeploymentRule().commands(dep, _graph(dep))
        assert any("rollout status" in c for c in cmds)

    def test_hypothesis_evidence_field_set(self):
        dep = Deployment(uid="d1", name="api", replicas=3, ready_replicas=0)
        hyp = RemediationEngine(rules=[_DegradedDeploymentRule()]).score(_graph(dep))[0]
        assert isinstance(hyp.evidence, list)


# ─────────────────────────────────────────────────────────────────────────────
# Engine — fault tolerance and multi-entity
# ─────────────────────────────────────────────────────────────────────────────

class TestRemediationEngineFaultTolerance:
    def test_rule_match_exception_skipped(self):
        """A rule that throws in match() must not abort scoring."""
        class _BrokenRule(_OOMKillRule):
            id = "broken"
            def match(self, entity, graph):
                raise RuntimeError("boom")

        pod = _oom_pod()
        g = _graph(pod)
        results = RemediationEngine(rules=[_BrokenRule(), _OOMKillRule()]).score(g)
        rule_ids = {h.rule_id for h in results}
        assert "oom_kill" in rule_ids
        assert "broken" not in rule_ids

    def test_rule_commands_exception_yields_empty_commands(self):
        """A rule that throws in commands() should produce a hypothesis with [] commands."""
        class _BrokenCmdsRule(_OOMKillRule):
            id = "broken_cmds"
            def commands(self, entity, graph):
                raise RuntimeError("no cmds")

        pod = _oom_pod()
        results = RemediationEngine(rules=[_BrokenCmdsRule()]).score(_graph(pod))
        assert len(results) == 1
        assert results[0].commands == []

    def test_same_rule_multiple_entities_all_appear(self):
        pod1 = _oom_pod(name="w-0")
        pod2 = _oom_pod(name="w-1")
        results = RemediationEngine(rules=[_OOMKillRule()]).score(_graph(pod1, pod2))
        affected = {h.affected for h in results}
        assert len(affected) == 2

    def test_empty_rules_list_returns_empty(self):
        pod = _oom_pod()
        assert RemediationEngine(rules=[]).score(_graph(pod)) == []

    def test_highest_weight_wins_dedup(self):
        """If the same (rule_id, uid) fires twice (shouldn't happen, but guard it)."""
        pod = _oom_pod()
        pod.annotations["drift.resources.limits.memory"] = "x"
        results = RemediationEngine(rules=[_OOMKillRule()]).score(_graph(pod))
        oom_hits = [h for h in results if h.rule_id == "oom_kill"]
        assert len(oom_hits) == 1
        assert oom_hits[0].weight > _OOMKillRule().base_weight  # boost applied

    def test_all_default_rules_exercised_on_full_scenario(self):
        oom   = _oom_pod(name="worker-0")
        img   = Pod(uid="p2", name="ml-0", phase="ImagePullBackOff")
        pend  = Pod(uid="p3", name="gpu-0", phase="Pending")
        crash = _crashloop_pod(name="api-0", restarts=6)
        notif = Pod(uid="p5", name="notif-0", namespace="prod", phase="Pending")
        dep   = Deployment(uid="d1", name="api-gateway", replicas=3, ready_replicas=0)

        ev_sched = _warning_event("gpu-0",  "Unschedulable", "no nodes available")
        ev_db    = _warning_event("api-0",  "BackOff",       "connection refused db:5432")
        ev_cfg   = _warning_event("notif-0","Failed",        "configmap notification-config not found")

        g = _graph(oom, img, pend, crash, notif, dep, ev_sched, ev_db, ev_cfg)
        results = RemediationEngine().score(g)
        rule_ids = {h.rule_id for h in results}

        assert "oom_kill"            in rule_ids
        assert "image_pull_backoff"  in rule_ids
        assert "pending_unschedulable" in rule_ids
        assert "crashloop_db"        in rule_ids
        assert "missing_config"      in rule_ids
        assert "degraded_deployment" in rule_ids


# ─────────────────────────────────────────────────────────────────────────────
# Missing coverage — edge cases
# ─────────────────────────────────────────────────────────────────────────────

from rca.remediation_engine import RemediationHypothesis, _Rule  # noqa: E402


class TestRemediationHypothesisStr:
    def test_str_with_explanation_and_commands(self):
        h = RemediationHypothesis(
            rule_id="oom_kill",
            symptom="Container OOMKilled",
            affected="Pod/prod/worker-0",
            weight=0.9,
            commands=["kubectl top pod worker-0 -n prod"],
            explanation="Increase memory limit.",
        )
        s = str(h)
        assert "[0.90]" in s
        assert "Container OOMKilled" in s
        assert "Increase memory limit." in s
        assert "kubectl top pod" in s

    def test_str_no_explanation_no_commands(self):
        h = RemediationHypothesis(
            rule_id="x", symptom="symptom", affected="a", weight=0.5, commands=[],
        )
        s = str(h)
        assert "[0.50]" in s
        assert "symptom" in s


class TestRuleBaseDefaults:
    def test_base_rule_match_returns_false(self):
        r = _Rule()
        assert r.match(Pod(uid="p1", name="p"), OntologyGraph()) is False

    def test_base_rule_commands_returns_empty(self):
        r = _Rule()
        assert r.commands(Pod(uid="p1", name="p"), OntologyGraph()) == []

    def test_base_rule_evidence_boosts_returns_empty(self):
        r = _Rule()
        assert r.evidence_boosts(Pod(uid="p1", name="p"), OntologyGraph()) == []


class TestCrashLoopDBCommandsWithAnnotation:
    def test_commands_include_db_service_when_env_db_host_set(self):
        pod = _crashloop_pod(restarts=8)
        pod.annotations["env.DB_HOST"] = "postgres.db-ns.svc.cluster.local"
        g = _graph(pod)
        cmds = _CrashLoopDBRule().commands(pod, g)
        assert any("postgres" in c for c in cmds)
        assert any("get pods" in c for c in cmds)

    def test_evidence_boost_restart_anomaly_annotation(self):
        pod = _crashloop_pod(restarts=8)
        pod.annotations["signal.restart_count"] = "anomaly=high"
        g = _graph(pod)
        boosts = _CrashLoopDBRule().evidence_boosts(pod, g)
        boost_descs = [d for d, _ in boosts]
        assert any("restart anomaly" in d for d in boost_descs)

    def test_evidence_boost_high_restart_count(self):
        pod = _crashloop_pod(restarts=12)
        g = _graph(pod)
        boosts = _CrashLoopDBRule().evidence_boosts(pod, g)
        boost_descs = [d for d, _ in boosts]
        assert any("10" in d for d in boost_descs)


class TestPendingSchedulingCommandsWithOwner:
    def test_commands_include_patch_when_owner_present(self):
        pod = Pod(uid="p1", name="api-xyz-abc", namespace="prod", phase="Pending",
                  owner_ref_kind="Deployment", owner_ref_name="api")
        dep = Deployment(uid="d1", name="api", namespace="prod",
                         replicas=2, ready_replicas=0)
        ev  = _warning_event("api-xyz-abc", "Unschedulable", "no nodes available")
        g = _graph(pod, dep, ev)
        cmds = _PendingSchedulingRule().commands(pod, g)
        assert any("nodeSelector" in c for c in cmds)

    def test_evidence_boost_unschedulable_event(self):
        pod = Pod(uid="p1", name="api-xyz", namespace="prod", phase="Pending")
        ev  = _warning_event("api-xyz", "Unschedulable", "0/3 nodes available")
        g = _graph(pod, ev)
        boosts = _PendingSchedulingRule().evidence_boosts(pod, g)
        assert any("Unschedulable" in d for d, _ in boosts)


class TestOwnerNameReplicaSetWalkUp:
    def test_replicaset_pod_walks_up_to_deployment(self):
        from rca.remediation_engine import _owner_name
        from ontology.entities import ResourceKind
        pod = Pod(uid="p1", name="api-xyz-abc12", namespace="prod",
                  owner_ref_kind="ReplicaSet", owner_ref_name="api-xyz")
        dep = Deployment(uid="d1", name="api-xyz", namespace="prod",
                         replicas=2, ready_replicas=1)
        g = _graph(pod, dep)
        g.add_entity(dep)
        assert _owner_name(pod, g) == "api-xyz"

    def test_non_pod_returns_empty(self):
        from rca.remediation_engine import _owner_name
        dep = Deployment(uid="d1", name="api", namespace="prod",
                         replicas=1, ready_replicas=1)
        assert _owner_name(dep, OntologyGraph()) == ""

    def test_pod_no_owner_returns_empty(self):
        from rca.remediation_engine import _owner_name
        pod = Pod(uid="p1", name="standalone", namespace="prod", owner_ref_kind="")
        assert _owner_name(pod, OntologyGraph()) == ""
