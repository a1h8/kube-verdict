"""
Unit tests for PrometheusCollector — all HTTP calls are mocked.
"""
from unittest.mock import MagicMock, patch

import requests

from ingestion.prometheus_collector import PrometheusCollector, _find_entity
from ontology.entities import Deployment, Pod, PrometheusAlert, Service, StatefulSet
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType


# ─────────────────────────────────────────────────────────────────────────���───
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_resp(json_data: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status = MagicMock()
    return r


def _alert(
    name: str = "KubePodCrashLooping",
    severity: str = "critical",
    state: str = "firing",
    namespace: str = "prod",
    pod: str = "api-xyz",
    summary: str = "Pod crashlooping",
) -> dict:
    return {
        "labels": {
            "alertname": name,
            "severity": severity,
            "namespace": namespace,
            "pod": pod,
        },
        "annotations": {"summary": summary},
        "state": state,
        "activeAt": "2026-05-10T08:00:00Z",
    }


def _graph_with_pod(name: str = "api-xyz", namespace: str = "prod") -> OntologyGraph:
    g = OntologyGraph()
    g.add_entity(Pod(uid="p1", name=name, namespace=namespace))
    return g


# ─────────────────────────────────────────────────────────────────────────────
# is_available
# ─────────────────────────────────────────────────────────────────────────────

class TestIsAvailable:
    def test_returns_true_on_200(self):
        c = PrometheusCollector(url="http://prom:9090")
        with patch("requests.get", return_value=_mock_resp({}, 200)):
            assert c.is_available() is True

    def test_returns_false_on_non_200(self):
        c = PrometheusCollector(url="http://prom:9090")
        with patch("requests.get", return_value=_mock_resp({}, 503)):
            assert c.is_available() is False

    def test_returns_false_on_connection_error(self):
        c = PrometheusCollector(url="http://prom:9090")
        with patch("requests.get", side_effect=requests.ConnectionError()):
            assert c.is_available() is False


# ─────────────────────────────────────────────────────────────────────────────
# _fetch_alerts
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchAlerts:
    def test_returns_alerts_list(self):
        c = PrometheusCollector(url="http://prom:9090")
        payload = {"data": {"alerts": [_alert()]}}
        with patch("requests.get", return_value=_mock_resp(payload)):
            result = c._fetch_alerts()
        assert len(result) == 1
        assert result[0]["labels"]["alertname"] == "KubePodCrashLooping"

    def test_returns_empty_on_timeout(self):
        c = PrometheusCollector(url="http://prom:9090")
        with patch("requests.get", side_effect=requests.Timeout()):
            assert c._fetch_alerts() == []

    def test_returns_empty_on_request_error(self):
        c = PrometheusCollector(url="http://prom:9090")
        with patch("requests.get", side_effect=requests.ConnectionError()):
            assert c._fetch_alerts() == []

    def test_returns_empty_when_no_data_key(self):
        c = PrometheusCollector(url="http://prom:9090")
        with patch("requests.get", return_value=_mock_resp({})):
            assert c._fetch_alerts() == []

    def test_sends_bearer_token(self):
        c = PrometheusCollector(url="http://prom:9090", token="secret")
        payload = {"data": {"alerts": []}}
        with patch("requests.get", return_value=_mock_resp(payload)) as mock_get:
            c._fetch_alerts()
        headers = mock_get.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret"

    def test_no_auth_header_without_token(self):
        c = PrometheusCollector(url="http://prom:9090")
        payload = {"data": {"alerts": []}}
        with patch("requests.get", return_value=_mock_resp(payload)) as mock_get:
            c._fetch_alerts()
        headers = mock_get.call_args[1]["headers"]
        assert "Authorization" not in headers


# ─────────────────────────────────────────────────────────────────────────────
# _correlate
# ─────────────────────────────────────────────────────────────────────────────

class TestCorrelate:
    def test_matches_by_pod_label(self):
        g = _graph_with_pod("api-xyz", "prod")
        c = PrometheusCollector(url="http://prom:9090")
        entity = c._correlate(
            {"pod": "api-xyz", "namespace": "prod"}, g
        )
        assert entity is not None
        assert entity.name == "api-xyz"

    def test_matches_by_deployment_label(self):
        g = OntologyGraph()
        g.add_entity(Deployment(uid="d1", name="api", namespace="prod"))
        c = PrometheusCollector(url="http://prom:9090")
        entity = c._correlate({"deployment": "api", "namespace": "prod"}, g)
        assert entity is not None
        assert entity.name == "api"

    def test_matches_by_service_label(self):
        g = OntologyGraph()
        g.add_entity(Service(uid="s1", name="api", namespace="prod", ports=[]))
        c = PrometheusCollector(url="http://prom:9090")
        entity = c._correlate({"service": "api", "namespace": "prod"}, g)
        assert entity is not None

    def test_matches_by_statefulset_label(self):
        g = OntologyGraph()
        g.add_entity(StatefulSet(uid="sts1", name="db", namespace="prod"))
        c = PrometheusCollector(url="http://prom:9090")
        entity = c._correlate({"statefulset": "db", "namespace": "prod"}, g)
        assert entity is not None

    def test_pod_takes_priority_over_deployment(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api-xyz", namespace="prod"))
        g.add_entity(Deployment(uid="d1", name="api", namespace="prod"))
        c = PrometheusCollector(url="http://prom:9090")
        entity = c._correlate(
            {"pod": "api-xyz", "deployment": "api", "namespace": "prod"}, g
        )
        assert entity.kind.value == "Pod"

    def test_returns_none_when_no_match(self):
        g = _graph_with_pod("api-xyz", "prod")
        c = PrometheusCollector(url="http://prom:9090")
        entity = c._correlate({"pod": "other-pod", "namespace": "prod"}, g)
        assert entity is None

    def test_namespace_mismatch_returns_none(self):
        g = _graph_with_pod("api-xyz", "prod")
        c = PrometheusCollector(url="http://prom:9090")
        entity = c._correlate({"pod": "api-xyz", "namespace": "staging"}, g)
        assert entity is None

    def test_no_labels_returns_none(self):
        g = _graph_with_pod()
        c = PrometheusCollector(url="http://prom:9090")
        assert c._correlate({}, g) is None


# ─────────────────────────────────────────────────────────────────────────────
# collect — full pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestCollect:
    def _setup(self, alerts: list[dict]):
        payload = {"data": {"alerts": alerts}}
        g = _graph_with_pod("api-xyz", "prod")
        c = PrometheusCollector(url="http://prom:9090")
        return c, g, payload

    def test_returns_correlated_count(self):
        c, g, payload = self._setup([_alert()])
        with patch("requests.get", return_value=_mock_resp(payload)):
            count = c.collect(g)
        assert count == 1

    def test_annotates_entity(self):
        c, g, payload = self._setup([_alert()])
        with patch("requests.get", return_value=_mock_resp(payload)):
            c.collect(g)
        pod = g.get("p1")
        assert pod.annotations.get("alert.KubePodCrashLooping.severity") == "critical"
        assert pod.annotations.get("alert.KubePodCrashLooping.state") == "firing"

    def test_creates_prometheus_alert_node(self):
        c, g, payload = self._setup([_alert()])
        with patch("requests.get", return_value=_mock_resp(payload)):
            c.collect(g)
        from ontology.entities import ResourceKind
        pa_nodes = [e for e in g.entities(ResourceKind.PROMETHEUS_ALERT)]
        assert len(pa_nodes) == 1
        assert pa_nodes[0].alert_name == "KubePodCrashLooping"
        assert pa_nodes[0].severity == "critical"

    def test_wires_has_alert_edge(self):
        c, g, payload = self._setup([_alert()])
        with patch("requests.get", return_value=_mock_resp(payload)):
            c.collect(g)
        edges = [e for e in g._adj.get("p1", [])
                 if e.rel_type == RelationshipType.HAS_ALERT]
        assert len(edges) == 1

    def test_edge_not_duplicated_on_second_collect(self):
        c, g, payload = self._setup([_alert()])
        with patch("requests.get", return_value=_mock_resp(payload)):
            c.collect(g)
            c.collect(g)
        edges = [e for e in g._adj.get("p1", [])
                 if e.rel_type == RelationshipType.HAS_ALERT]
        assert len(edges) == 1

    def test_pending_alerts_skipped(self):
        c, g, payload = self._setup([_alert(state="pending")])
        with patch("requests.get", return_value=_mock_resp(payload)):
            count = c.collect(g)
        assert count == 0

    def test_unmatched_alert_not_counted(self):
        c, g, payload = self._setup([_alert(pod="unknown-pod")])
        with patch("requests.get", return_value=_mock_resp(payload)):
            count = c.collect(g)
        assert count == 0

    def test_empty_alerts_returns_zero(self):
        c, g, _ = self._setup([])
        payload = {"data": {"alerts": []}}
        with patch("requests.get", return_value=_mock_resp(payload)):
            assert c.collect(g) == 0

    def test_multiple_alerts_different_pods(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api-1", namespace="prod"))
        g.add_entity(Pod(uid="p2", name="api-2", namespace="prod"))
        alerts = [
            _alert(pod="api-1"),
            _alert(name="KubeOOMKilling", severity="warning", pod="api-2"),
        ]
        payload = {"data": {"alerts": alerts}}
        c = PrometheusCollector(url="http://prom:9090")
        with patch("requests.get", return_value=_mock_resp(payload)):
            count = c.collect(g)
        assert count == 2

    def test_same_alert_two_pods_single_node_created(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api-1", namespace="prod"))
        g.add_entity(Pod(uid="p2", name="api-2", namespace="prod"))
        alerts = [
            _alert(pod="api-1"),
            _alert(pod="api-2"),  # same alertname + namespace
        ]
        payload = {"data": {"alerts": alerts}}
        c = PrometheusCollector(url="http://prom:9090")
        with patch("requests.get", return_value=_mock_resp(payload)):
            c.collect(g)
        from ontology.entities import ResourceKind
        pa_nodes = list(g.entities(ResourceKind.PROMETHEUS_ALERT))
        assert len(pa_nodes) == 1  # deduplicated by (alertname, namespace)

    def test_summary_annotation_set(self):
        c, g, payload = self._setup([_alert(summary="Pod is restarting frequently")])
        with patch("requests.get", return_value=_mock_resp(payload)):
            c.collect(g)
        pod = g.get("p1")
        assert pod.annotations.get("alert.KubePodCrashLooping.summary") == "Pod is restarting frequently"

    def test_fetch_failure_returns_zero(self):
        c, g, _ = self._setup([])
        with patch("requests.get", side_effect=requests.ConnectionError()):
            assert c.collect(g) == 0


# ─────────────────────────────────────────────────────────────────────────────
# _find_entity
# ─────────────────────────────────────────────────────────────────────────────

class TestFindEntity:
    def test_finds_by_kind_name_namespace(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api", namespace="prod"))
        assert _find_entity(g, "Pod", "api", "prod") is not None

    def test_returns_none_for_wrong_kind(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api", namespace="prod"))
        assert _find_entity(g, "Deployment", "api", "prod") is None

    def test_empty_namespace_matches_any(self):
        g = OntologyGraph()
        g.add_entity(Pod(uid="p1", name="api", namespace="prod"))
        assert _find_entity(g, "Pod", "api", "") is not None


# ─────────────────────────────────────────────────────────────────────────────
# PrometheusAlert entity
# ─────────────────────────────────────────────────────────────────────────────

class TestPrometheusAlertEntity:
    def test_to_text_includes_name_severity(self):
        pa = PrometheusAlert(
            uid="prom-alert-CrashLoop-prod",
            name="CrashLoop",
            namespace="prod",
            alert_name="CrashLoop",
            severity="critical",
            state="firing",
            summary="Pod is crashlooping",
        )
        text = pa.to_text()
        assert "CrashLoop" in text
        assert "critical" in text
        assert "firing" in text
        assert "prod" in text

    def test_to_text_includes_summary(self):
        pa = PrometheusAlert(
            uid="prom-alert-x-ns",
            name="X",
            alert_name="X",
            severity="warning",
            state="firing",
            summary="Disk almost full",
        )
        assert "Disk almost full" in pa.to_text()

    def test_to_text_includes_extra_labels(self):
        pa = PrometheusAlert(
            uid="prom-alert-x-ns",
            name="X",
            alert_name="X",
            severity="warning",
            state="firing",
            alert_labels={"alertname": "X", "severity": "warning", "pod": "api-0"},
        )
        text = pa.to_text()
        assert "pod=api-0" in text
        # alertname label should be filtered (it's already in the name= field)
        assert "alertname=X" not in text
