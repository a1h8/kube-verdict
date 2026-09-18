from api.models import SignalAlert
from api.signal_mapper import anomalous_signals, signal_to_namespaces, signal_to_query


def _signal(severity="critical", entity_uid="Pod/checkout/api-7c9", metric_name="cpu_usage", **labels) -> SignalAlert:
    return SignalAlert(
        entity_uid=entity_uid,
        metric_name=metric_name,
        ts=1_700_000_000_000,
        severity=severity,
        score=3.2,
        method="patchtst",
        labels=labels,
    )


# ── signal_to_query ──────────────────────────────────────────────────────────

def test_query_includes_metric_name():
    q = signal_to_query(_signal(metric_name="restart_count"))
    assert "restart_count" in q


def test_query_includes_pod_label():
    q = signal_to_query(_signal(pod="api-7c9", namespace="checkout"))
    assert "pod/api-7c9" in q
    assert "namespace checkout" in q


def test_query_includes_deployment_label():
    q = signal_to_query(_signal(deployment="payment-service", namespace="prod"))
    assert "deployment/payment-service" in q


def test_query_falls_back_to_entity_uid_without_resource_labels():
    q = signal_to_query(_signal(entity_uid="Pod/checkout/api-7c9"))
    assert "Pod/checkout/api-7c9" in q


def test_query_includes_severity():
    q = signal_to_query(_signal(severity="critical"))
    assert "[CRITICAL]" in q


def test_query_includes_text_when_present():
    s = _signal()
    s = s.model_copy(update={"text": "forecast residual spiking"})
    q = signal_to_query(s)
    assert "forecast residual spiking" in q


def test_query_exported_namespace_fallback():
    q = signal_to_query(_signal(exported_namespace="monitoring"))
    assert "namespace monitoring" in q


# ── signal_to_namespaces ─────────────────────────────────────────────────────

def test_namespaces_from_label():
    ns = signal_to_namespaces(_signal(namespace="staging"))
    assert ns == ["staging"]


def test_namespaces_empty_when_absent():
    ns = signal_to_namespaces(_signal())
    assert ns == []


def test_namespaces_exported_namespace():
    ns = signal_to_namespaces(_signal(exported_namespace="infra"))
    assert ns == ["infra"]


# ── anomalous_signals ────────────────────────────────────────────────────────

def test_anomalous_filters_out_normal():
    signals = [_signal(severity="normal"), _signal(severity="warning"), _signal(severity="critical")]
    result = anomalous_signals(signals)
    assert len(result) == 2
    assert all(s.severity != "normal" for s in result)


def test_all_normal_returns_empty():
    signals = [_signal(severity="normal"), _signal(severity="normal")]
    assert anomalous_signals(signals) == []
