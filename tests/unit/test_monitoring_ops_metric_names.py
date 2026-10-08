"""
Monitoring Ops panels checked offline (B15) — docs/test-cases.md,
"Broadened offline coverage" §2.

Live, only 2 of the 5 panels of helm/kube-verdict/dashboards/monitoring-ops.json were
ever seen with real data. This test runs the real telemetry.instrument() against an
in-memory metric reader (OTLP exporters stubbed, nothing leaves the process), drives
the same code paths a live run would — HTTP requests incl. a 5xx, both LLM call sites,
a collector fallback through workflow.nodes._stats() — then checks that every metric,
label matcher and `by (...)` label referenced by a panel query is actually produced.
A renamed metric or attribute now fails here instead of showing as an empty panel.
"""
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics.export import Histogram, InMemoryMetricReader, Sum

import telemetry

_DASHBOARD = Path(__file__).resolve().parents[2] / "helm/kube-verdict/dashboards/monitoring-ops.json"

# OTLP unit → Prometheus suffix, as the OTel Collector's Prometheus exporter
# normalizes it. Limited to the units kube-verdict emits; `{...}` annotations are dropped.
_UNIT_SUFFIX = {"ms": "milliseconds", "s": "seconds", "By": "bytes", "": ""}


def _prom_base_name(name: str, unit: str) -> str:
    base = name.replace(".", "_")
    suffix = "" if unit.startswith("{") else _UNIT_SUFFIX[unit]
    if suffix and not base.endswith("_" + suffix):
        base = f"{base}_{suffix}"
    return base


def _prom_series(metrics_data) -> dict[str, list[dict[str, str]]]:
    """Prometheus series name → label sets, as Prometheus would see them after the
    collector (job = service.name, attribute dots → underscores)."""
    series: dict[str, list[dict[str, str]]] = {}
    for rm in metrics_data.resource_metrics:
        job = rm.resource.attributes["service.name"]
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                base = _prom_base_name(m.name, m.unit or "")
                if isinstance(m.data, Histogram):
                    names = [f"{base}_bucket", f"{base}_count", f"{base}_sum"]
                elif isinstance(m.data, Sum) and m.data.is_monotonic:
                    names = [f"{base}_total"]
                else:
                    names = [base]
                for dp in m.data.data_points:
                    labels = {k.replace(".", "_"): str(v) for k, v in dp.attributes.items()}
                    labels["job"] = job
                    for n in names:
                        series.setdefault(n, []).append(labels)
    return series


_BY = re.compile(r"\bby\s*\(([^)]*)\)")
# After `by (...)` clauses are removed, a metric selector is an identifier followed by
# an optional `{matchers}` and then a range `[` or a closing `)`; functions are followed by `(`.
_SELECTOR = re.compile(r"([a-zA-Z_:][\w:]*)\s*(?:\{([^}]*)\})?(?=\s*[\[)])")
_MATCHER = re.compile(r'(\w+)\s*(=~|!~|!=|=)\s*"([^"]*)"')


def _panel_queries() -> list[tuple[str, str]]:
    dashboard = json.loads(_DASHBOARD.read_text())
    return [(p["title"], t["expr"]) for p in dashboard["panels"] for t in p.get("targets", [])]


def _parse(expr: str) -> tuple[list[tuple[str, list[tuple[str, str, str]]]], set[str]]:
    by_labels = {lbl.strip() for group in _BY.findall(expr) for lbl in group.split(",") if lbl.strip()}
    selectors = [
        (name, _MATCHER.findall(matchers or ""))
        for name, matchers in _SELECTOR.findall(_BY.sub("", expr))
    ]
    return selectors, by_labels - {"le"}


def _matches(labels: dict[str, str], op: str, key: str, value: str) -> bool:
    actual = labels.get(key, "")
    return {
        "=": actual == value,
        "!=": actual != value,
        "=~": re.fullmatch(value, actual) is not None,
        "!~": re.fullmatch(value, actual) is None,
    }[op]


@pytest.fixture(scope="module")
def series(monkeypatch_module):
    monkeypatch_module.setattr("config.OTEL_SELF_MONITORING_ENABLED", True)
    reader = InMemoryMetricReader()
    providers = {}
    real_instrument_app = FastAPIInstrumentor.instrument_app

    def instrument_app_with_captured_providers(app, **_):
        # telemetry.instrument() relies on the global providers; the global can only be
        # set once per process, so hand the providers it built to the instrumentor directly.
        return real_instrument_app(
            app, meter_provider=providers["meter"], tracer_provider=providers["tracer"]
        )

    with patch("opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader", return_value=reader), \
         patch("opentelemetry.exporter.otlp.proto.grpc.metric_exporter.OTLPMetricExporter"), \
         patch("opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"), \
         patch("opentelemetry.metrics.set_meter_provider", side_effect=lambda p: providers.__setitem__("meter", p)), \
         patch("opentelemetry.trace.set_tracer_provider", side_effect=lambda p: providers.__setitem__("tracer", p)), \
         patch.object(FastAPIInstrumentor, "instrument_app", side_effect=instrument_app_with_captured_providers):
        app = FastAPI()

        @app.get("/healthz")
        def healthz():
            return {"ok": True}

        @app.get("/boom")
        def boom():
            raise HTTPException(status_code=500)

        telemetry.instrument(app)

    try:
        client = TestClient(app)
        assert client.get("/healthz").status_code == 200
        assert client.get("/boom").status_code == 500
        telemetry.record_llm_call_duration(1.5, node="hypothesize")
        telemetry.record_llm_call_duration(0.8, node="analyze")
        from workflow.nodes import _stats
        _stats({}, "loki", {"fallback": True})
        yield _prom_series(reader.get_metrics_data())
    finally:
        telemetry._llm_call_duration = None
        telemetry._collector_fallback_counter = None


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_dashboard_has_the_five_panels():
    titles = [title for title, _ in _panel_queries()]
    assert len(titles) == 5, titles


@pytest.mark.parametrize("title,expr", _panel_queries(), ids=[t for t, _ in _panel_queries()])
def test_panel_query_is_satisfied_by_emitted_metrics(series, title, expr):
    selectors, by_labels = _parse(expr)
    assert selectors, f"no metric selector parsed from {expr!r}"
    for name, matchers in selectors:
        assert name in series, f"[{title}] {name} is never emitted; emitted: {sorted(series)}"
        candidates = [
            labels for labels in series[name]
            if all(_matches(labels, op, key, value) for key, op, value in matchers)
        ]
        assert candidates, f"[{title}] no {name} series matches {matchers}; got {series[name]}"
        for label in by_labels:
            assert any(label in labels for labels in candidates), (
                f"[{title}] {name} has no `{label}` label to group by; got {candidates}"
            )


def test_llm_duration_is_recorded_for_both_call_sites(series):
    nodes = {labels["node"] for labels in series["kubeverdict_llm_call_duration_seconds_bucket"]}
    assert nodes == {"hypothesize", "analyze"}


def test_collector_fallback_comes_through_the_real_stats_choke_point(series):
    collectors = {labels["collector"] for labels in series["kubeverdict_collector_fallback_total"]}
    assert collectors == {"loki"}
