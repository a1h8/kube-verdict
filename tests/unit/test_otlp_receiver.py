"""
Unit tests for OtlpReceiver — HTTP server and OtelBackend interface.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone

import pytest

from ingestion.otlp_receiver import OtlpReceiver, _kv_to_dict, _str_val, _ts_in_range


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _span(
    trace_id: str = "abc123",
    name: str = "GET /api",
    is_error: bool = False,
    error_msg: str = "",
    start_nano: int = 1_700_000_000_000_000_000,
    end_nano: int   = 1_700_000_000_100_000_000,
) -> dict:
    span = {
        "traceId": trace_id,
        "spanId": "span1",
        "name": name,
        "startTimeUnixNano": str(start_nano),
        "endTimeUnixNano": str(end_nano),
        "status": {},
    }
    if is_error:
        span["status"] = {"code": 2, "message": error_msg}
    return span


def _payload(
    service: str = "checkout",
    spans: list[dict] | None = None,
) -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service}}
                    ]
                },
                "scopeSpans": [{"spans": spans or [_span()]}],
            }
        ]
    }


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(port: int, path: str, body: dict) -> int:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


# ─────────────────────────────────────────────────────────────────────────────
# OtlpReceiver — lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestOtlpReceiverLifecycle:
    def test_is_available_after_start(self):
        port = _free_port()
        r = OtlpReceiver(host="127.0.0.1", port=port)
        assert not r.is_available()
        r.start()
        time.sleep(0.1)
        assert r.is_available()
        r.stop()

    def test_is_available_false_after_stop(self):
        port = _free_port()
        r = OtlpReceiver(host="127.0.0.1", port=port)
        r.start()
        time.sleep(0.1)
        r.stop()
        assert not r.is_available()

    def test_http_200_on_valid_post(self):
        port = _free_port()
        r = OtlpReceiver(host="127.0.0.1", port=port)
        r.start()
        time.sleep(0.1)
        try:
            status = _post(port, "/v1/traces", _payload())
            assert status == 200
        finally:
            r.stop()

    def test_http_404_on_unknown_path(self):
        port = _free_port()
        r = OtlpReceiver(host="127.0.0.1", port=port)
        r.start()
        time.sleep(0.1)
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                _post(port, "/v1/metrics", _payload())
            assert exc_info.value.code == 404
        finally:
            r.stop()


# ─────────────────────────────────────────────────────────────────────────────
# OtlpReceiver — span ingestion
# ─────────────────────────────────────────────────────────────────────────────

class TestOtlpReceiverIngestion:
    def _receiver(self) -> OtlpReceiver:
        port = _free_port()
        r = OtlpReceiver(host="127.0.0.1", port=port)
        r.start()
        time.sleep(0.05)
        return r

    def test_trace_appears_after_post(self):
        r = self._receiver()
        try:
            _post(r._port, "/v1/traces", _payload(spans=[_span(trace_id="t1")]))
            time.sleep(0.05)
            assert r.get_trace("t1") is not None
        finally:
            r.stop()

    def test_error_span_sets_status_error(self):
        r = self._receiver()
        try:
            _post(r._port, "/v1/traces", _payload(spans=[
                _span(trace_id="terr", is_error=True, error_msg="db refused")
            ]))
            time.sleep(0.05)
            t = r.get_trace("terr")
            assert t is not None
            assert t["status"] == "ERROR"
            assert "db refused" in t["error_message"]
        finally:
            r.stop()

    def test_ok_span_sets_status_ok(self):
        r = self._receiver()
        try:
            _post(r._port, "/v1/traces", _payload(spans=[_span(trace_id="tok")]))
            time.sleep(0.05)
            t = r.get_trace("tok")
            assert t is not None
            assert t["status"] == "OK"
        finally:
            r.stop()

    def test_span_count_increments_across_posts(self):
        r = self._receiver()
        try:
            _post(r._port, "/v1/traces", _payload(spans=[
                _span(trace_id="tmulti", name="span-1"),
                _span(trace_id="tmulti", name="span-2"),
            ]))
            time.sleep(0.05)
            t = r.get_trace("tmulti")
            assert t is not None
            assert t["span_count"] == 2
        finally:
            r.stop()

    def test_service_name_captured(self):
        r = self._receiver()
        try:
            _post(r._port, "/v1/traces", _payload(service="order-service", spans=[_span(trace_id="tsvc")]))
            time.sleep(0.05)
            t = r.get_trace("tsvc")
            assert t is not None
            assert t["service_name"] == "order-service"
        finally:
            r.stop()

    def test_duration_ms_set(self):
        r = self._receiver()
        try:
            start = 1_700_000_000_000_000_000
            end   = 1_700_000_000_500_000_000  # 500 ms
            _post(r._port, "/v1/traces", _payload(spans=[
                _span(trace_id="tdur", start_nano=start, end_nano=end)
            ]))
            time.sleep(0.05)
            t = r.get_trace("tdur")
            assert t is not None
            assert abs(t["duration_ms"] - 500.0) < 1.0
        finally:
            r.stop()

    def test_started_at_is_iso(self):
        r = self._receiver()
        try:
            _post(r._port, "/v1/traces", _payload(spans=[_span(trace_id="tiso")]))
            time.sleep(0.05)
            t = r.get_trace("tiso")
            assert t is not None
            assert "T" in t["started_at"]
        finally:
            r.stop()


# ─────────────────────────────────────────────────────────────────────────────
# OtlpReceiver — OtelBackend interface
# ─────────────────────────────────────────────────────────────────────────────

class TestOtlpReceiverBackendInterface:
    def _populated_receiver(self) -> OtlpReceiver:
        port = _free_port()
        r = OtlpReceiver(host="127.0.0.1", port=port)
        r.start()
        time.sleep(0.05)
        now = int(time.time())
        _post(r._port, "/v1/traces", _payload(
            service="checkout",
            spans=[_span(
                trace_id="err-trace",
                is_error=True,
                error_msg="timeout",
                start_nano=now * 1_000_000_000,
                end_nano=(now + 1) * 1_000_000_000,
            )],
        ))
        _post(r._port, "/v1/traces", _payload(
            service="auth",
            spans=[_span(
                trace_id="ok-trace",
                is_error=False,
                start_nano=now * 1_000_000_000,
                end_nano=(now + 1) * 1_000_000_000,
            )],
        ))
        time.sleep(0.05)
        return r

    def test_search_error_traces_returns_errors_only(self):
        r = self._populated_receiver()
        try:
            now = int(time.time())
            results = r.search_error_traces("checkout", "prod", now - 60, now + 60)
            assert any(t["trace_id"] == "err-trace" for t in results)
            assert all(t["status"] == "ERROR" for t in results)
        finally:
            r.stop()

    def test_search_error_traces_filters_by_service(self):
        r = self._populated_receiver()
        try:
            now = int(time.time())
            results = r.search_error_traces("auth", "prod", now - 60, now + 60)
            assert all(t["trace_id"] != "err-trace" for t in results)
        finally:
            r.stop()

    def test_get_trace_returns_none_for_unknown(self):
        r = self._populated_receiver()
        try:
            assert r.get_trace("does-not-exist") is None
        finally:
            r.stop()

    def test_get_trace_returns_known(self):
        r = self._populated_receiver()
        try:
            t = r.get_trace("err-trace")
            assert t is not None
            assert t["service_name"] == "checkout"
        finally:
            r.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Ring buffer eviction
# ─────────────────────────────────────────────────────────────────────────────

class TestOtlpReceiverEviction:
    def test_evicts_oldest_when_full(self):
        r = OtlpReceiver(max_traces=3)
        for i in range(4):
            r._ingest(_payload(spans=[_span(trace_id=f"t{i}")]))
        assert r.get_trace("t0") is None   # evicted
        assert r.get_trace("t3") is not None

    def test_buffer_never_exceeds_max(self):
        r = OtlpReceiver(max_traces=5)
        for i in range(20):
            r._ingest(_payload(spans=[_span(trace_id=f"t{i}")]))
        assert len(r._traces) <= 5


# ─────────────────────────────────────────────────────────────────────────────
# Protobuf + gzip wire formats (the OTLP/HTTP defaults)
# ─────────────────────────────────────────────────────────────────────────────

def _protobuf_body(
    service: str = "checkout",
    trace_id_hex: str = "0123456789abcdef0123456789abcdef",
    is_error: bool = False,
    error_msg: str = "boom",
) -> bytes:
    """Build a serialised OTLP/HTTP protobuf ExportTraceServiceRequest."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    rs.resource.attributes.add(key="service.name").value.string_value = service
    sp = rs.scope_spans.add().spans.add()
    sp.trace_id = bytes.fromhex(trace_id_hex)
    sp.span_id = bytes.fromhex("0123456789abcdef")
    sp.name = "GET /api"
    sp.start_time_unix_nano = 1_700_000_000_000_000_000
    sp.end_time_unix_nano = 1_700_000_000_100_000_000
    if is_error:
        sp.status.code = 2  # STATUS_CODE_ERROR
        sp.status.message = error_msg
    return req.SerializeToString()


def _post_raw(port: int, body: bytes, content_type: str, content_encoding: str | None = None) -> int:
    headers = {"Content-Type": content_type}
    if content_encoding:
        headers["Content-Encoding"] = content_encoding
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/traces", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


class TestOtlpProtobuf:
    def _receiver(self) -> OtlpReceiver:
        port = _free_port()
        r = OtlpReceiver(host="127.0.0.1", port=port)
        r.start()
        time.sleep(0.05)
        return r

    def test_protobuf_post_returns_200(self):
        r = self._receiver()
        try:
            status = _post_raw(r._port, _protobuf_body(), "application/x-protobuf")
            assert status == 200
        finally:
            r.stop()

    def test_protobuf_span_is_ingested(self):
        r = self._receiver()
        try:
            _post_raw(r._port, _protobuf_body(service="order-service"), "application/x-protobuf")
            time.sleep(0.05)
            assert len(r._traces) == 1
            t = next(iter(r._traces.values()))
            assert t["service_name"] == "order-service"
            assert "T" in t["started_at"]
            assert abs(t["duration_ms"] - 100.0) < 1.0
        finally:
            r.stop()

    def test_protobuf_error_status_detected(self):
        r = self._receiver()
        try:
            _post_raw(r._port, _protobuf_body(is_error=True, error_msg="db refused"),
                      "application/x-protobuf")
            time.sleep(0.05)
            t = next(iter(r._traces.values()))
            assert t["status"] == "ERROR"
            assert "db refused" in t["error_message"]
        finally:
            r.stop()

    def test_malformed_protobuf_returns_400(self):
        r = self._receiver()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                _post_raw(r._port, b"not-a-protobuf", "application/x-protobuf")
            assert exc_info.value.code == 400
        finally:
            r.stop()


class TestOtlpGzip:
    def _receiver(self) -> OtlpReceiver:
        port = _free_port()
        r = OtlpReceiver(host="127.0.0.1", port=port)
        r.start()
        time.sleep(0.05)
        return r

    def test_gzip_json_is_ingested(self):
        import gzip as _gz
        r = self._receiver()
        try:
            body = _gz.compress(json.dumps(_payload(spans=[_span(trace_id="tgz")])).encode())
            status = _post_raw(r._port, body, "application/json", content_encoding="gzip")
            assert status == 200
            time.sleep(0.05)
            assert r.get_trace("tgz") is not None
        finally:
            r.stop()

    def test_gzip_protobuf_is_ingested(self):
        import gzip as _gz
        r = self._receiver()
        try:
            body = _gz.compress(_protobuf_body(service="gz-svc"))
            status = _post_raw(r._port, body, "application/x-protobuf", content_encoding="gzip")
            assert status == 200
            time.sleep(0.05)
            assert len(r._traces) == 1
        finally:
            r.stop()

    def test_invalid_gzip_returns_400(self):
        r = self._receiver()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                _post_raw(r._port, b"\x1f\x8bnotgzip", "application/json", content_encoding="gzip")
            assert exc_info.value.code == 400
        finally:
            r.stop()


# ─────────────────────────────────────────────────────────────────────────────
# build_backend factory
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildBackendOtlp:
    def test_factory_returns_otlp_receiver(self):
        from ingestion.otel_backend import build_backend
        b = build_backend("otlp", "http://ignored", otlp_host="127.0.0.1", otlp_port=_free_port())
        try:
            assert isinstance(b, OtlpReceiver)
            assert b.is_available()  # singleton is started, not just constructed
        finally:
            b.stop()

    def test_factory_reuses_singleton_per_host_port(self):
        from ingestion.otel_backend import build_backend
        port = _free_port()
        a = build_backend("otlp", "http://ignored", otlp_host="127.0.0.1", otlp_port=port)
        b = build_backend("otlp", "http://ignored", otlp_host="127.0.0.1", otlp_port=port)
        try:
            assert a is b  # same started receiver, buffer survives across runs
        finally:
            a.stop()

    def test_factory_tempo_unchanged(self):
        from ingestion.otel_backend import build_backend, TempoBackend
        b = build_backend("tempo", "http://localhost:3100")
        assert isinstance(b, TempoBackend)

    def test_factory_jaeger_unchanged(self):
        from ingestion.otel_backend import build_backend, JaegerBackend
        b = build_backend("jaeger", "http://localhost:16686")
        assert isinstance(b, JaegerBackend)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_kv_to_dict(self):
        attrs = [
            {"key": "service.name", "value": {"stringValue": "api"}},
            {"key": "k8s.namespace", "value": {"stringValue": "prod"}},
        ]
        d = _kv_to_dict(attrs)
        assert d["service.name"] == "api"
        assert d["k8s.namespace"] == "prod"

    def test_str_val_string(self):
        assert _str_val({"stringValue": "hello"}) == "hello"

    def test_str_val_int(self):
        assert _str_val({"intValue": 42}) == "42"

    def test_str_val_bool(self):
        assert _str_val({"boolValue": True}) == "true"

    def test_ts_in_range_no_timestamp(self):
        assert _ts_in_range("", 0, 9999999999) is True

    def test_ts_in_range_within(self):
        now = int(time.time())
        iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        assert _ts_in_range(iso, now - 60, now + 60) is True

    def test_ts_in_range_outside(self):
        now = int(time.time())
        iso = datetime.fromtimestamp(now - 3600, tz=timezone.utc).isoformat()
        assert _ts_in_range(iso, now - 60, now + 60) is False
