"""
Unit tests for OtelBackend (TempoBackend, JaegerBackend) — all HTTP calls mocked.
"""
from unittest.mock import MagicMock, patch

import pytest

from ingestion.otel_backend import (
    JaegerBackend,
    TempoBackend,
    _micro_to_iso,
    _nano_to_iso,
    _normalise_jaeger_trace,
    _normalise_tempo_trace,
    build_backend,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_resp(json_data: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status = MagicMock()
    return r


def _tempo_trace(trace_id: str = "abc123", with_error: bool = True) -> dict:
    error_code = 2 if with_error else 1
    return {
        "traceID": trace_id,
        "batches": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "checkout"}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "POST /checkout",
                                "startTimeUnixNano": "1715000000000000000",
                                "endTimeUnixNano":   "1715000000500000000",
                                "status": {"code": error_code, "message": "DB timeout"},
                                "events": [],
                            }
                        ]
                    }
                ],
            }
        ],
    }


def _jaeger_trace(trace_id: str = "def456", with_error: bool = True) -> dict:
    tags = [{"key": "error", "value": True}] if with_error else []
    return {
        "traceID": trace_id,
        "spans": [
            {
                "operationName": "GET /order",
                "processID": "p1",
                "startTime": 1715000000000000,
                "duration":  500000,
                "tags": tags,
            }
        ],
        "processes": {"p1": {"serviceName": "order-svc"}},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Timestamp helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestTimestampHelpers:
    def test_nano_to_iso_valid(self):
        iso = _nano_to_iso(1715000000000000000)
        assert iso.startswith("2024-") or iso.startswith("2025-")

    def test_nano_to_iso_string_input(self):
        result = _nano_to_iso("1715000000000000000")
        assert "T" in result

    def test_nano_to_iso_bad_input_returns_empty(self):
        assert _nano_to_iso("not-a-number") == ""

    def test_micro_to_iso_valid(self):
        iso = _micro_to_iso(1715000000000000)
        assert "T" in iso

    def test_micro_to_iso_zero_returns_something(self):
        result = _micro_to_iso(0)
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# _normalise_tempo_trace
# ─────────────────────────────────────────────────────────────────────────────

class TestNormaliseTempoTrace:
    def test_error_trace_status(self):
        result = _normalise_tempo_trace(_tempo_trace(with_error=True))
        assert result["status"] == "ERROR"

    def test_ok_trace_status(self):
        result = _normalise_tempo_trace(_tempo_trace(with_error=False))
        assert result["status"] == "OK"

    def test_trace_id_extracted(self):
        result = _normalise_tempo_trace(_tempo_trace(trace_id="myid"))
        assert result["trace_id"] == "myid"

    def test_service_name_extracted(self):
        result = _normalise_tempo_trace(_tempo_trace())
        assert result["service_name"] == "checkout"

    def test_duration_ms_positive(self):
        result = _normalise_tempo_trace(_tempo_trace())
        assert result["duration_ms"] > 0

    def test_span_count(self):
        result = _normalise_tempo_trace(_tempo_trace())
        assert result["span_count"] == 1

    def test_root_span_name(self):
        result = _normalise_tempo_trace(_tempo_trace(with_error=True))
        assert result["root_span"] == "POST /checkout"

    def test_error_message(self):
        result = _normalise_tempo_trace(_tempo_trace(with_error=True))
        assert result["error_message"] == "DB timeout"

    def test_error_spans_list(self):
        result = _normalise_tempo_trace(_tempo_trace(with_error=True))
        assert len(result["error_spans"]) == 1
        assert result["error_spans"][0]["name"] == "POST /checkout"

    def test_exception_message_from_event(self):
        trace = {
            "traceID": "x",
            "batches": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "name": "op",
                                    "startTimeUnixNano": "1715000000000000000",
                                    "endTimeUnixNano":   "1715000000100000000",
                                    "status": {"code": 2, "message": ""},
                                    "events": [
                                        {
                                            "attributes": [
                                                {
                                                    "key": "exception.message",
                                                    "value": {"stringValue": "NullPointerException"},
                                                }
                                            ]
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ],
        }
        result = _normalise_tempo_trace(trace)
        assert result["error_message"] == "NullPointerException"

    def test_empty_batches(self):
        result = _normalise_tempo_trace({"traceID": "empty", "batches": []})
        assert result["status"] == "OK"
        assert result["span_count"] == 0

    def test_status_code_string(self):
        trace = {
            "traceID": "t",
            "batches": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "name": "op",
                                    "startTimeUnixNano": "1715000000000000000",
                                    "endTimeUnixNano":   "1715000000100000000",
                                    "status": {"code": "STATUS_CODE_ERROR", "message": "fail"},
                                    "events": [],
                                }
                            ]
                        }
                    ],
                }
            ],
        }
        result = _normalise_tempo_trace(trace)
        assert result["status"] == "ERROR"


# ─────────────────────────────────────────────────────────────────────────────
# _normalise_jaeger_trace
# ─────────────────────────────────────────────────────────────────────────────

class TestNormaliseJaegerTrace:
    def test_error_trace_status(self):
        result = _normalise_jaeger_trace(_jaeger_trace(with_error=True))
        assert result["status"] == "ERROR"

    def test_ok_trace_status(self):
        result = _normalise_jaeger_trace(_jaeger_trace(with_error=False))
        assert result["status"] == "OK"

    def test_trace_id(self):
        result = _normalise_jaeger_trace(_jaeger_trace(trace_id="def456"))
        assert result["trace_id"] == "def456"

    def test_service_name(self):
        result = _normalise_jaeger_trace(_jaeger_trace())
        assert result["service_name"] == "order-svc"

    def test_duration_ms(self):
        result = _normalise_jaeger_trace(_jaeger_trace())
        assert result["duration_ms"] == pytest.approx(500.0)

    def test_span_count(self):
        result = _normalise_jaeger_trace(_jaeger_trace())
        assert result["span_count"] == 1

    def test_error_spans_populated(self):
        result = _normalise_jaeger_trace(_jaeger_trace(with_error=True))
        assert result["error_spans"][0]["name"] == "GET /order"

    def test_root_span_from_error_spans(self):
        result = _normalise_jaeger_trace(_jaeger_trace(with_error=True))
        assert result["root_span"] == "GET /order"

    def test_empty_spans(self):
        data = {"traceID": "e", "spans": [], "processes": {}}
        result = _normalise_jaeger_trace(data)
        assert result["status"] == "OK"
        assert result["span_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# TempoBackend
# ─────────────────────────────────────────────────────────────────────────────

class TestTempoBackend:
    def test_is_available_true(self):
        b = TempoBackend(url="http://tempo:3100")
        with patch("requests.get", return_value=_mock_resp({}, 200)):
            assert b.is_available() is True

    def test_is_available_false_on_error(self):
        import requests as req
        b = TempoBackend(url="http://tempo:3100")
        with patch("requests.get", side_effect=req.ConnectionError):
            assert b.is_available() is False

    def test_get_trace_returns_normalised(self):
        b = TempoBackend(url="http://tempo:3100")
        with patch("requests.get", return_value=_mock_resp(_tempo_trace("tid1"))):
            result = b.get_trace("tid1")
        assert result is not None
        assert result["trace_id"] == "tid1"
        assert result["status"] == "ERROR"

    def test_get_trace_returns_none_on_failure(self):
        import requests as req
        b = TempoBackend(url="http://tempo:3100")
        with patch("requests.get", side_effect=req.ConnectionError):
            assert b.get_trace("missing") is None

    def test_search_error_traces(self):
        search_resp = {"traces": [{"traceID": "abc123"}]}
        trace_resp = _tempo_trace("abc123")
        responses = [_mock_resp(search_resp), _mock_resp(trace_resp)]
        b = TempoBackend(url="http://tempo:3100")
        with patch("requests.get", side_effect=responses):
            traces = b.search_error_traces("svc", "ns", 1000, 2000)
        assert len(traces) == 1
        assert traces[0]["trace_id"] == "abc123"

    def test_search_error_traces_empty_on_no_hits(self):
        b = TempoBackend(url="http://tempo:3100")
        with patch("requests.get", return_value=_mock_resp({"traces": []})):
            traces = b.search_error_traces("svc", "ns", 1000, 2000)
        assert traces == []

    def test_search_error_traces_skips_empty_trace_id(self):
        search_resp = {"traces": [{"traceID": ""}]}
        b = TempoBackend(url="http://tempo:3100")
        with patch("requests.get", return_value=_mock_resp(search_resp)):
            traces = b.search_error_traces("svc", "ns", 1000, 2000)
        assert traces == []

    def test_bearer_token_in_headers(self):
        b = TempoBackend(url="http://tempo:3100", token="mytoken")
        assert b._headers() == {"Authorization": "Bearer mytoken"}

    def test_no_token_empty_headers(self):
        b = TempoBackend(url="http://tempo:3100")
        assert b._headers() == {}


# ─────────────────────────────────────────────────────────────────────────────
# JaegerBackend
# ─────────────────────────────────────────────────────────────────────────────

class TestJaegerBackend:
    def test_is_available_checks_services_endpoint(self):
        b = JaegerBackend(url="http://jaeger:16686")
        with patch("requests.get", return_value=_mock_resp({}, 200)) as mock_get:
            result = b.is_available()
        assert result is True
        assert "/api/services" in mock_get.call_args[0][0]

    def test_is_available_false_on_error(self):
        import requests as req
        b = JaegerBackend(url="http://jaeger:16686")
        with patch("requests.get", side_effect=req.ConnectionError):
            assert b.is_available() is False

    def test_get_trace_returns_normalised(self):
        b = JaegerBackend(url="http://jaeger:16686")
        resp = {"data": [_jaeger_trace("def456")]}
        with patch("requests.get", return_value=_mock_resp(resp)):
            result = b.get_trace("def456")
        assert result is not None
        assert result["trace_id"] == "def456"

    def test_get_trace_returns_none_on_empty_data(self):
        b = JaegerBackend(url="http://jaeger:16686")
        with patch("requests.get", return_value=_mock_resp({"data": []})):
            assert b.get_trace("missing") is None

    def test_search_error_traces_microseconds(self):
        resp = {"data": [_jaeger_trace("j1")]}
        b = JaegerBackend(url="http://jaeger:16686")
        with patch("requests.get", return_value=_mock_resp(resp)) as mock_get:
            b.search_error_traces("svc", "ns", 1000, 2000)
        params = mock_get.call_args[1]["params"]
        assert params["start"] == 1000 * 1_000_000
        assert params["end"]   == 2000 * 1_000_000

    def test_search_error_traces_returns_list(self):
        resp = {"data": [_jaeger_trace("j1"), _jaeger_trace("j2")]}
        b = JaegerBackend(url="http://jaeger:16686")
        with patch("requests.get", return_value=_mock_resp(resp)):
            traces = b.search_error_traces("svc", "ns", 0, 9999)
        assert len(traces) == 2

    def test_search_error_traces_empty_on_failure(self):
        import requests as req
        b = JaegerBackend(url="http://jaeger:16686")
        with patch("requests.get", side_effect=req.ConnectionError):
            traces = b.search_error_traces("svc", "ns", 0, 9999)
        assert traces == []


# ─────────────────────────────────────────────────────────────────────────────
# build_backend factory
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildBackend:
    def test_jaeger_type(self):
        b = build_backend("jaeger", "http://jaeger:16686")
        assert isinstance(b, JaegerBackend)

    def test_jaeger_case_insensitive(self):
        b = build_backend("JAEGER", "http://jaeger:16686")
        assert isinstance(b, JaegerBackend)

    def test_tempo_default(self):
        b = build_backend("tempo", "http://tempo:3100")
        assert isinstance(b, TempoBackend)

    def test_unknown_defaults_to_tempo(self):
        b = build_backend("zipkin", "http://zipkin:9411")
        assert isinstance(b, TempoBackend)

    def test_token_passed_through(self):
        b = build_backend("tempo", "http://tempo:3100", token="tok")
        assert b.token == "tok"

    def test_timeout_passed_through(self):
        b = build_backend("jaeger", "http://jaeger:16686", timeout=60)
        assert b.timeout == 60

    def test_url_trailing_slash_stripped(self):
        b = build_backend("tempo", "http://tempo:3100/")
        assert b.url == "http://tempo:3100"
