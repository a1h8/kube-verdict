"""
Self-observability (B15) — kube-verdict's own OTLP metrics/traces.

Distinct from ingestion/otlp_receiver.py, which ingests traces PUSHED BY monitored
applications as RCA evidence. This module exports kube-verdict's own request
latency/error rate (via FastAPI auto-instrumentation), LLM call duration, and
collector fallback counts, so a Grafana dashboard can show whether the service
itself is healthy — ahead of a real GCP/sovereign-cloud deployment
(docs/cloud-prerequisites.md).

No-op unless OTEL_SELF_MONITORING_ENABLED=true: instrument() returns immediately,
and the record_* functions are safe to call unconditionally from anywhere in the
codebase (workflow, ingestion) without checking the flag themselves.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import config as cfg

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

_llm_call_duration = None
_collector_fallback_counter = None


def instrument(app: "FastAPI") -> None:
    """Wire OTLP tracing + metrics into the FastAPI app. Safe to call unconditionally."""
    if not cfg.OTEL_SELF_MONITORING_ENABLED:
        return
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "OTEL_SELF_MONITORING_ENABLED=true but the opentelemetry SDK is not installed; "
            "skipping self-observability instrumentation"
        )
        return

    resource = Resource.create({"service.name": cfg.OTEL_SERVICE_NAME})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=cfg.OTEL_EXPORTER_OTLP_ENDPOINT))
    )
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=cfg.OTEL_EXPORTER_OTLP_ENDPOINT)
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    meter = meter_provider.get_meter("kube-verdict")
    global _llm_call_duration, _collector_fallback_counter
    _llm_call_duration = meter.create_histogram(
        "kubeverdict.llm.call.duration",
        unit="s",
        description="Duration of LLM calls made during hypothesis generation and analysis",
    )
    _collector_fallback_counter = meter.create_counter(
        "kubeverdict.collector.fallback",
        description="Count of collector fallbacks (collector failed, degraded evidence)",
    )

    FastAPIInstrumentor.instrument_app(app)
    logger.info("OTLP self-monitoring active (endpoint=%s)", cfg.OTEL_EXPORTER_OTLP_ENDPOINT)


def record_llm_call_duration(seconds: float, node: str) -> None:
    if _llm_call_duration is not None:
        _llm_call_duration.record(seconds, attributes={"node": node})


def record_collector_fallback(collector: str) -> None:
    if _collector_fallback_counter is not None:
        _collector_fallback_counter.add(1, attributes={"collector": collector})
