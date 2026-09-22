"""
Telemetry gate — which pods LokiSource / OtelCollector pull logs and traces for.

Pod.is_unhealthy is phase-only (it feeds BFS seeds, remediation rules and the
golden baselines), so a pod that is Running but failing its readiness probe, or
crashlooping, stays "healthy" by that definition even though its logs and traces
are exactly what an RCA needs. needs_telemetry widens only the collectors'
target selection and leaves is_unhealthy untouched.
"""
from unittest.mock import MagicMock, patch

from ingestion.loki_source import LokiSource
from ingestion.otel_collector import OtelCollector, _is_unhealthy
from ontology.entities import Pod
from ontology.graph import OntologyGraph

READY = [{"name": "app", "ready": True}]
NOT_READY = [{"name": "app", "ready": False}]
# container_statuses as the live k8s_collector builds them
LIVE_SHAPE = [{"name": "app", "ready": False, "restart_count": 4, "state": "waiting"}]
# container_statuses as the fixture loader keeps them (raw kubectl)
KUBECTL_SHAPE = [{
    "name": "app", "ready": False, "restartCount": 4,
    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
}]


def _pod(name="api-0", phase="Running", statuses=None) -> Pod:
    return Pod(
        uid=f"pod-{name}", name=name, namespace="prod", phase=phase,
        container_statuses=statuses if statuses is not None else [],
    )


def _graph(*pods: Pod) -> OntologyGraph:
    g = OntologyGraph()
    for p in pods:
        g.add_entity(p)
    return g


class TestPodProperties:
    def test_running_and_ready_needs_nothing(self):
        p = _pod(statuses=READY)
        assert not p.is_unhealthy
        assert not p.is_not_ready
        assert not p.needs_telemetry

    def test_running_not_ready_is_flagged_but_not_unhealthy(self):
        p = _pod(statuses=NOT_READY)
        assert not p.is_unhealthy          # phase-only definition is unchanged
        assert p.is_not_ready
        assert p.needs_telemetry

    def test_crashloop_in_live_shape_is_flagged(self):
        assert _pod(statuses=LIVE_SHAPE).needs_telemetry

    def test_crashloop_in_kubectl_shape_is_flagged(self):
        assert _pod(statuses=KUBECTL_SHAPE).needs_telemetry

    def test_one_container_not_ready_is_enough(self):
        p = _pod(statuses=READY + [{"name": "sidecar", "ready": False}])
        assert p.needs_telemetry

    def test_running_without_container_statuses_is_not_flagged(self):
        assert not _pod(statuses=[]).needs_telemetry

    def test_pending_is_still_targeted(self):
        p = _pod(phase="Pending")
        assert p.is_unhealthy
        assert not p.is_not_ready
        assert p.needs_telemetry

    def test_failed_is_still_targeted(self):
        assert _pod(phase="Failed").needs_telemetry

    def test_completed_pod_is_not_flagged(self):
        """A finished job's containers report ready=False; that is not an incident."""
        p = _pod(phase="Succeeded", statuses=NOT_READY)
        assert not p.needs_telemetry


class TestLokiTargetSelection:
    def _collect(self, graph):
        src = LokiSource(url="http://loki.invalid")
        with patch.object(LokiSource, "_query", return_value=[]) as q:
            src.collect(graph)
        return q

    def test_ready_pod_is_not_queried(self):
        q = self._collect(_graph(_pod(statuses=READY)))
        q.assert_not_called()

    def test_running_not_ready_pod_is_queried(self):
        q = self._collect(_graph(_pod(name="api-0", statuses=NOT_READY)))
        q.assert_called_once()
        assert 'k8s_pod_name="api-0"' in q.call_args.args[0]

    def test_pending_pod_is_still_queried(self):
        q = self._collect(_graph(_pod(phase="Pending")))
        q.assert_called_once()

    def test_only_the_not_ready_pod_of_a_mixed_graph_is_queried(self):
        q = self._collect(_graph(
            _pod(name="ok-0", statuses=READY),
            _pod(name="bad-0", statuses=NOT_READY),
        ))
        assert q.call_count == 1
        assert 'k8s_pod_name="bad-0"' in q.call_args.args[0]


class TestOtelTargetSelection:
    def test_gate_follows_needs_telemetry_for_pods(self):
        assert _is_unhealthy(_pod(statuses=NOT_READY)) is True
        assert _is_unhealthy(_pod(statuses=READY)) is False
        assert _is_unhealthy(_pod(phase="Pending")) is True

    def _backend_calls(self, *pods: Pod) -> int:
        backend = MagicMock()
        backend.search_error_traces.return_value = []
        OtelCollector(backend).collect(_graph(*pods))
        return backend.search_error_traces.call_count

    def test_ready_pod_is_not_searched(self):
        assert self._backend_calls(_pod(statuses=READY)) == 0

    def test_running_not_ready_pod_is_searched(self):
        assert self._backend_calls(_pod(statuses=NOT_READY)) == 1
