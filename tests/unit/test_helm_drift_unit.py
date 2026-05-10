"""
Unit tests for HelmDriftDetector — no cluster, no Helm CLI.
All tests use synthetic OntologyGraph entities.
"""
import pytest

from ingestion.helm_drift import HelmDriftDetector, _first_int, _resolve_dot_path
from ontology.entities import (
    DaemonSet, Deployment, DriftItem, HelmChart, HelmRelease,
    PersistentVolumeClaim, Pod, StatefulSet, ChartDependency,
)
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _graph_with_release(release: HelmRelease, *entities) -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(release)
    for e in entities:
        g.add_entity(e)
        g.add_edge(Edge(e.uid, release.uid, RelationshipType.MANAGED_BY_HELM))
    return g


# ─────────────────────────────────────────────────────────────────────────────
# _check_deployment
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckDeployment:
    def test_no_drift_when_healthy(self):
        dep = Deployment(uid="d1", name="api", namespace="prod",
                         replicas=3, ready_replicas=3)
        drifts = HelmDriftDetector._check_deployment(
            dep, {"replicaCount": "3"}, "api"
        )
        assert drifts == []

    def test_replica_count_mismatch(self):
        dep = Deployment(uid="d1", name="api", namespace="prod",
                         replicas=3, ready_replicas=3)
        drifts = HelmDriftDetector._check_deployment(
            dep, {"replicaCount": "5"}, "api"
        )
        assert any(d.field_path == "spec.replicas" for d in drifts)

    def test_ready_replicas_less_than_desired(self):
        dep = Deployment(uid="d1", name="api", namespace="prod",
                         replicas=3, ready_replicas=1)
        drifts = HelmDriftDetector._check_deployment(dep, {}, "api")
        ready_drifts = [d for d in drifts if "readyReplicas" in d.field_path]
        assert len(ready_drifts) == 1
        assert ready_drifts[0].severity == "warning"

    def test_zero_ready_replicas_is_critical(self):
        dep = Deployment(uid="d1", name="api", namespace="prod",
                         replicas=3, ready_replicas=0)
        drifts = HelmDriftDetector._check_deployment(dep, {}, "api")
        ready_drifts = [d for d in drifts if "readyReplicas" in d.field_path]
        assert ready_drifts[0].severity == "critical"

    def test_uses_release_namespaced_key(self):
        dep = Deployment(uid="d1", name="api", namespace="prod",
                         replicas=2, ready_replicas=2)
        drifts = HelmDriftDetector._check_deployment(
            dep, {"api.replicaCount": "5"}, "api"
        )
        assert any(d.field_path == "spec.replicas" for d in drifts)


# ─────────────────────────────────────────────────────────────────────────────
# _check_statefulset
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckStatefulSet:
    def test_healthy_no_drift(self):
        sts = StatefulSet(uid="s1", name="db", namespace="prod",
                          replicas=3, ready_replicas=3)
        assert HelmDriftDetector._check_statefulset(sts, {}, "db") == []

    def test_not_ready_is_warning(self):
        sts = StatefulSet(uid="s1", name="db", namespace="prod",
                          replicas=3, ready_replicas=2)
        drifts = HelmDriftDetector._check_statefulset(sts, {}, "db")
        assert drifts[0].severity == "warning"

    def test_zero_ready_is_critical(self):
        sts = StatefulSet(uid="s1", name="db", namespace="prod",
                          replicas=3, ready_replicas=0)
        drifts = HelmDriftDetector._check_statefulset(sts, {}, "db")
        assert drifts[0].severity == "critical"


# ─────────────────────────────────────────────────────────────────────────────
# _check_daemonset
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckDaemonSet:
    def test_healthy_no_drift(self):
        ds = DaemonSet(uid="ds1", name="log", namespace="kube-system",
                       desired=3, ready=3)
        assert HelmDriftDetector._check_daemonset(ds, {}, "log") == []

    def test_not_ready_warning(self):
        ds = DaemonSet(uid="ds1", name="log", namespace="kube-system",
                       desired=3, ready=2)
        drifts = HelmDriftDetector._check_daemonset(ds, {}, "log")
        assert drifts[0].severity == "warning"

    def test_zero_ready_critical(self):
        ds = DaemonSet(uid="ds1", name="log", namespace="kube-system",
                       desired=3, ready=0)
        drifts = HelmDriftDetector._check_daemonset(ds, {}, "log")
        assert drifts[0].severity == "critical"


# ─────────────────────────────────────────────────────────────────────────────
# _check_pvc
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckPVC:
    def test_bound_pvc_no_drift(self):
        pvc = PersistentVolumeClaim(uid="pvc1", name="data", namespace="prod",
                                    status_phase="Bound", requested_storage="10Gi")
        assert HelmDriftDetector._check_pvc(pvc, {"persistence.enabled": "true"}) == []

    def test_pending_pvc_critical(self):
        pvc = PersistentVolumeClaim(uid="pvc1", name="data", namespace="prod",
                                    status_phase="Pending", requested_storage="10Gi")
        drifts = HelmDriftDetector._check_pvc(pvc, {"persistence.enabled": "true"})
        assert drifts[0].severity == "critical"
        assert drifts[0].field_path == "status.phase"

    def test_pvc_disabled_no_drift(self):
        pvc = PersistentVolumeClaim(uid="pvc1", name="data", namespace="prod",
                                    status_phase="Pending", requested_storage="10Gi")
        drifts = HelmDriftDetector._check_pvc(pvc, {"persistence.enabled": "false"})
        assert drifts == []

    def test_storage_size_drift(self):
        pvc = PersistentVolumeClaim(uid="pvc1", name="data", namespace="prod",
                                    status_phase="Bound", requested_storage="5Gi")
        drifts = HelmDriftDetector._check_pvc(
            pvc, {"persistence.enabled": "true", "persistence.size": "10Gi"}
        )
        size_drifts = [d for d in drifts if "storage" in d.field_path]
        assert len(size_drifts) == 1
        assert size_drifts[0].declared == "10Gi"
        assert size_drifts[0].observed == "5Gi"


# ─────────────────────────────────────────────────────────────────────────────
# _check_pod
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckPod:
    def test_healthy_pod_no_drift(self):
        pod = Pod(uid="p1", name="api-xyz", namespace="prod",
                  restart_count=0, container_statuses=[])
        assert HelmDriftDetector._check_pod(pod, {}) == []

    def test_crashloop_detected(self):
        pod = Pod(uid="p1", name="api-xyz", namespace="prod",
                  restart_count=3,
                  container_statuses=[{"name": "api", "state": "CrashLoopBackOff"}])
        drifts = HelmDriftDetector._check_pod(pod, {})
        crash_drifts = [d for d in drifts if "CrashLoopBackOff" in str(d.observed)]
        assert len(crash_drifts) == 1
        assert crash_drifts[0].severity == "critical"

    def test_oomkilled_detected(self):
        pod = Pod(uid="p1", name="api-xyz", namespace="prod",
                  restart_count=2,
                  container_statuses=[{"name": "api", "state": "OOMKilled"}])
        drifts = HelmDriftDetector._check_pod(pod, {})
        oom_drifts = [d for d in drifts if "OOMKilled" in str(d.observed)]
        assert len(oom_drifts) == 1

    def test_high_restarts_warning(self):
        pod = Pod(uid="p1", name="api-xyz", namespace="prod",
                  restart_count=10, container_statuses=[])
        drifts = HelmDriftDetector._check_pod(pod, {})
        restart_drifts = [d for d in drifts if "restartCount" in d.field_path]
        assert restart_drifts[0].severity == "warning"

    def test_very_high_restarts_critical(self):
        pod = Pod(uid="p1", name="api-xyz", namespace="prod",
                  restart_count=25, container_statuses=[])
        drifts = HelmDriftDetector._check_pod(pod, {})
        restart_drifts = [d for d in drifts if "restartCount" in d.field_path]
        assert restart_drifts[0].severity == "critical"

    def test_low_restarts_no_drift(self):
        pod = Pod(uid="p1", name="api-xyz", namespace="prod",
                  restart_count=3, container_statuses=[])
        drifts = HelmDriftDetector._check_pod(pod, {})
        assert drifts == []


# ─────────────────────────────────────────────────────────────────────────────
# _check_subchart_conditions
# ─────────────────────────────────────────────────────────────────────────────

class TestSubchartConditions:
    def _make_chart(self) -> HelmChart:
        return HelmChart(
            uid="chart-api-1.0", name="api",
            dependencies=[
                ChartDependency(name="postgresql", version="13.0.0",
                                condition="postgresql.enabled"),
            ],
        )

    def test_enabled_but_missing_is_warning(self):
        g = OntologyGraph()
        release = HelmRelease(uid="helm-api", name="api", namespace="prod",
                              values={"postgresql": {"enabled": True}})
        chart = self._make_chart()
        g.add_entity(release)
        g.add_entity(chart)

        drifts = HelmDriftDetector._check_subchart_conditions(
            g, chart, {"postgresql": {"enabled": True}}, "prod", release
        )
        assert any("postgresql" in d.field_path for d in drifts)
        assert drifts[0].severity == "warning"

    def test_disabled_with_resources_is_info(self):
        g = OntologyGraph()
        release = HelmRelease(uid="helm-api", name="api", namespace="prod")
        chart = self._make_chart()
        g.add_entity(release)
        g.add_entity(chart)
        # Add a pod with postgresql label
        pod = Pod(uid="pg-pod", name="pg-0", namespace="prod",
                  labels={"app.kubernetes.io/name": "postgresql"})
        g.add_entity(pod)

        drifts = HelmDriftDetector._check_subchart_conditions(
            g, chart, {"postgresql": {"enabled": False}}, "prod", release
        )
        info_drifts = [d for d in drifts if d.severity == "info"]
        assert len(info_drifts) == 1

    def test_no_condition_skipped(self):
        g = OntologyGraph()
        chart = HelmChart(
            uid="chart-api-1.0", name="api",
            dependencies=[ChartDependency(name="redis", version="1.0.0")],
        )
        release = HelmRelease(uid="helm-api", name="api", namespace="prod")
        g.add_entity(release)

        drifts = HelmDriftDetector._check_subchart_conditions(
            g, chart, {}, "prod", release
        )
        assert drifts == []


# ─────────────────────────────────────────────────────────────────────────────
# detect_all — full pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectAll:
    def test_detect_all_returns_count(self):
        release = HelmRelease(
            uid="helm-prod-api", name="api", namespace="prod",
            values={"replicaCount": 5},
        )
        dep = Deployment(uid="dep-api", name="api", namespace="prod",
                         replicas=3, ready_replicas=1)
        g = _graph_with_release(release, dep)

        count = HelmDriftDetector().detect_all(g)
        assert count > 0

    def test_detect_all_annotates_entity(self):
        release = HelmRelease(uid="helm-prod-api", name="api", namespace="prod",
                              values={})
        dep = Deployment(uid="dep-api", name="api", namespace="prod",
                         replicas=3, ready_replicas=0)
        g = _graph_with_release(release, dep)
        HelmDriftDetector().detect_all(g)

        assert any(k.startswith("drift.") for k in dep.annotations)

    def test_detect_all_adds_drifts_from_edge(self):
        release = HelmRelease(uid="helm-prod-api", name="api", namespace="prod",
                              values={})
        dep = Deployment(uid="dep-api", name="api", namespace="prod",
                         replicas=3, ready_replicas=0)
        g = _graph_with_release(release, dep)
        HelmDriftDetector().detect_all(g)

        drift_edges = [
            e for e in g._adj.get("dep-api", [])
            if e.rel_type == RelationshipType.DRIFTS_FROM
        ]
        assert len(drift_edges) == 1

    def test_drifts_from_edge_not_duplicated(self):
        release = HelmRelease(uid="helm-prod-api", name="api", namespace="prod",
                              values={})
        dep = Deployment(uid="dep-api", name="api", namespace="prod",
                         replicas=3, ready_replicas=0)
        g = _graph_with_release(release, dep)
        detector = HelmDriftDetector()
        detector.detect_all(g)
        detector.detect_all(g)  # run twice

        drift_edges = [
            e for e in g._adj.get("dep-api", [])
            if e.rel_type == RelationshipType.DRIFTS_FROM
        ]
        assert len(drift_edges) == 1

    def test_no_drift_on_healthy_graph(self):
        release = HelmRelease(uid="helm-prod-api", name="api", namespace="prod",
                              values={"replicaCount": 3})
        dep = Deployment(uid="dep-api", name="api", namespace="prod",
                         replicas=3, ready_replicas=3)
        g = _graph_with_release(release, dep)
        count = HelmDriftDetector().detect_all(g)
        assert count == 0

    def test_pvc_drift_detected(self):
        release = HelmRelease(uid="helm-api", name="api", namespace="prod",
                              values={"persistence": {"enabled": True}})
        pvc = PersistentVolumeClaim(uid="pvc-1", name="data", namespace="prod",
                                    status_phase="Pending", requested_storage="10Gi")
        g = _graph_with_release(release, pvc)
        count = HelmDriftDetector().detect_all(g)
        assert count > 0


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

class TestFirstInt:
    def test_finds_first_matching_key(self):
        assert _first_int({"replicaCount": "3"}, ["replicaCount"]) == 3

    def test_returns_none_when_no_key(self):
        assert _first_int({}, ["replicaCount"]) is None

    def test_skips_non_int_values(self):
        assert _first_int({"replicaCount": "abc", "replicas": "2"}, ["replicaCount", "replicas"]) == 2

    def test_tries_multiple_keys(self):
        assert _first_int({"replicas": "5"}, ["replicaCount", "replicas"]) == 5


class TestResolveDotPath:
    def test_resolves_simple_key(self):
        assert _resolve_dot_path({"enabled": True}, "enabled") is True

    def test_resolves_nested_key(self):
        assert _resolve_dot_path({"postgresql": {"enabled": True}}, "postgresql.enabled") is True

    def test_returns_none_for_missing(self):
        assert _resolve_dot_path({}, "missing.key") is None

    def test_returns_none_for_partial_path(self):
        assert _resolve_dot_path({"postgresql": "string"}, "postgresql.enabled") is None
