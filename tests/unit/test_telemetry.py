"""
Unit tests for telemetry.py — kube-verdict's self-observability OTLP export (B15).

Distinct from ingestion/otlp_receiver.py (evidence ingestion): this module exports
kube-verdict's own request/LLM/collector metrics. No-op by default so it never
becomes a startup dependency for the rest of the test suite.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

import telemetry


@pytest.fixture(autouse=True)
def _reset_globals():
    """instrument() sets module-level globals — keep tests isolated."""
    telemetry._llm_call_duration = None
    telemetry._collector_fallback_counter = None
    yield
    telemetry._llm_call_duration = None
    telemetry._collector_fallback_counter = None


def test_instrument_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr("config.OTEL_SELF_MONITORING_ENABLED", False)
    with patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app") as m:
        telemetry.instrument(FastAPI())
    m.assert_not_called()
    assert telemetry._llm_call_duration is None
    assert telemetry._collector_fallback_counter is None


def test_record_functions_are_safe_noop_before_instrumentation():
    # Must never raise, even though no meter has been created.
    telemetry.record_llm_call_duration(1.23, node="hypothesize")
    telemetry.record_collector_fallback("prometheus")


def test_instrument_wires_exporters_and_meters_when_enabled(monkeypatch):
    monkeypatch.setattr("config.OTEL_SELF_MONITORING_ENABLED", True)
    monkeypatch.setattr("config.OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setattr("config.OTEL_SERVICE_NAME", "kube-verdict-api")

    with patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app") as instrument_app, \
         patch("opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter") as span_exp, \
         patch("opentelemetry.exporter.otlp.proto.grpc.metric_exporter.OTLPMetricExporter") as metric_exp:
        app = FastAPI()
        telemetry.instrument(app)

    span_exp.assert_called_once_with(endpoint="http://collector:4317")
    metric_exp.assert_called_once_with(endpoint="http://collector:4317")
    instrument_app.assert_called_once_with(app)
    assert telemetry._llm_call_duration is not None
    assert telemetry._collector_fallback_counter is not None


def test_record_llm_call_duration_records_on_the_histogram():
    telemetry._llm_call_duration = MagicMock()
    telemetry.record_llm_call_duration(0.42, node="analyze")
    telemetry._llm_call_duration.record.assert_called_once_with(0.42, attributes={"node": "analyze"})


def test_record_collector_fallback_increments_the_counter():
    telemetry._collector_fallback_counter = MagicMock()
    telemetry.record_collector_fallback("loki")
    telemetry._collector_fallback_counter.add.assert_called_once_with(1, attributes={"collector": "loki"})
