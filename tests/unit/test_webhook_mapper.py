from api.models import AlertmanagerAlert
import config
from api.webhook_mapper import alert_text, alert_to_namespaces, alert_to_query, firing_alerts


def _alert(status="firing", **labels) -> AlertmanagerAlert:
    return AlertmanagerAlert(status=status, labels=labels)


# ── alert_to_query ─────────────────────────────────────────────────────────────

def test_query_alertname_only():
    q = alert_to_query(_alert(alertname="PodCrashLooping"))
    assert "PodCrashLooping" in q


def test_query_includes_deployment():
    q = alert_to_query(_alert(alertname="PodCrashLooping", deployment="payment-service", namespace="prod"))
    assert "deployment/payment-service" in q
    assert "namespace prod" in q


def test_query_includes_pod():
    q = alert_to_query(_alert(alertname="OOMKilled", pod="payment-abc", namespace="checkout"))
    assert "pod/payment-abc" in q


def test_query_severity_critical():
    q = alert_to_query(_alert(alertname="HighRestartCount", severity="critical"))
    assert "[CRITICAL]" in q


def test_query_severity_warning():
    q = alert_to_query(_alert(alertname="HighMemory", severity="warning"))
    assert "[WARNING]" in q


def test_query_leaves_the_summary_to_the_alert_text():          # fix plan H71
    a = AlertmanagerAlert(
        status="firing",
        labels={"alertname": "ImagePullBackOff"},
        annotations={"summary": "Image not found in registry"},
    )
    assert "Image not found in registry" not in alert_to_query(a)
    assert alert_text(a) == "Image not found in registry"


def test_query_no_labels_does_not_crash():
    q = alert_to_query(AlertmanagerAlert(status="firing", labels={}))
    assert q == "UnknownAlert"


def test_query_exported_namespace_fallback():
    q = alert_to_query(_alert(alertname="DiskFull", exported_namespace="monitoring"))
    assert "namespace monitoring" in q


# ── alert_to_namespaces ───────────────────────────────────────────────────────

def test_namespaces_from_label():
    ns = alert_to_namespaces(_alert(alertname="X", namespace="staging"))
    assert ns == ["staging"]


def test_namespaces_empty_when_absent_and_none_configured(monkeypatch):
    monkeypatch.setattr(config, "KUBE_NAMESPACES", [])
    ns = alert_to_namespaces(_alert(alertname="X"))
    assert ns == []


def test_namespaces_exported_namespace():
    ns = alert_to_namespaces(_alert(alertname="X", exported_namespace="infra"))
    assert ns == ["infra"]


# ── firing_alerts ─────────────────────────────────────────────────────────────

def test_firing_only():
    alerts = [_alert(alertname="A"), _alert(status="resolved", alertname="B"), _alert(alertname="C")]
    result = firing_alerts(alerts)
    assert len(result) == 2
    assert all(a.status == "firing" for a in result)


def test_all_resolved_returns_empty():
    alerts = [_alert(status="resolved", alertname="A"), _alert(status="resolved", alertname="B")]
    assert firing_alerts(alerts) == []
