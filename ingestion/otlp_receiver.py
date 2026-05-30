"""
OTLP/HTTP receiver — accepts spans pushed via POST /v1/traces.

Handles both wire formats accepted by OTLP/HTTP:
  * protobuf  (Content-Type: application/x-protobuf)  — the SDK/Collector default
  * JSON      (Content-Type: application/json)
plus gzip request bodies (Content-Encoding: gzip). Protobuf decoding requires
the `opentelemetry-proto` package.

Runs as a background thread; acts as an OtelBackend so OtelCollector works
unchanged. Spans are held in a fixed-size in-memory ring buffer (default 2 000).

Spec: https://opentelemetry.io/docs/specs/otlp/#otlphttp

Usage
-----
    receiver = OtlpReceiver(host="0.0.0.0", port=4318)
    receiver.start()                    # non-blocking
    collector = OtelCollector(receiver, lookback_hours=1)
    collector.collect(graph)
    receiver.stop()

Config env vars (read by config.py, forwarded by build_backend):
    OTLP_HOST          default 0.0.0.0
    OTLP_PORT          default 4318
    OTLP_MAX_SPANS     default 2000
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from ingestion.otel_backend import OtelBackend

log = logging.getLogger(__name__)

_DEFAULT_MAX_SPANS = 2_000


class _DecodeError(Exception):
    """Raised when a request body cannot be parsed as OTLP JSON or protobuf."""


class OtlpReceiver(OtelBackend):
    """
    OTLP/HTTP JSON receiver that doubles as an OtelBackend.

    The HTTP server runs in a daemon thread; spans are stored in a bounded
    deque keyed by trace_id → normalised dict.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 4318,
        max_spans: int = _DEFAULT_MAX_SPANS,
    ) -> None:
        # OtelBackend.__init__ expects url, token, timeout — pass dummy values
        super().__init__(url=f"http://{host}:{port}", token=None, timeout=30)
        self._host = host
        self._port = port
        self._max_spans = max_spans
        self._lock = threading.Lock()
        self._traces: dict[str, dict] = {}           # trace_id → normalised
        self._arrival_order: deque[str] = deque()    # trace_ids in arrival order
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path not in ("/v1/traces", "/v1/traces/"):
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                if self.headers.get("Content-Encoding", "").lower() == "gzip":
                    try:
                        body = gzip.decompress(body)
                    except (OSError, EOFError):
                        self.send_response(400)
                        self.end_headers()
                        return

                content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
                is_protobuf = content_type in ("application/x-protobuf", "application/protobuf")
                try:
                    payload = _decode_protobuf(body) if is_protobuf else json.loads(body)
                except (_DecodeError, json.JSONDecodeError):
                    self.send_response(400)
                    self.end_headers()
                    return

                receiver._ingest(payload)
                self.send_response(200)
                if is_protobuf:
                    # Spec wants an (empty) ExportTraceServiceResponse on success.
                    self.send_header("Content-Type", "application/x-protobuf")
                    self.end_headers()
                    self.wfile.write(_empty_protobuf_response())
                else:
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ANN401
                log.debug("otlp: " + fmt, *args)

        self._server = HTTPServer((self._host, self._port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="otlp-receiver"
        )
        self._thread.start()
        log.info("otlp: receiver started on %s:%d", self._host, self._port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        log.info("otlp: receiver stopped")

    def is_available(self) -> bool:
        return self._server is not None

    # ------------------------------------------------------------------
    # OtelBackend interface
    # ------------------------------------------------------------------

    def search_error_traces(
        self,
        service: str,
        namespace: str,
        start_ts: int,
        end_ts: int,
        limit: int = 20,
    ) -> list[dict]:
        with self._lock:
            results = [
                t for t in self._traces.values()
                if t.get("status") == "ERROR"
                and (not service or t.get("service_name", "") == service
                     or service in t.get("service_name", ""))
                and _ts_in_range(t.get("started_at", ""), start_ts, end_ts)
            ]
        return results[:limit]

    def get_trace(self, trace_id: str) -> dict | None:
        with self._lock:
            return self._traces.get(trace_id)

    # ------------------------------------------------------------------
    # Internal ingestion
    # ------------------------------------------------------------------

    def _ingest(self, payload: dict) -> None:
        for resource_span in payload.get("resourceSpans", []):
            resource_attrs = _kv_to_dict(
                resource_span.get("resource", {}).get("attributes", [])
            )
            service_name = resource_attrs.get("service.name", "")

            for scope_span in resource_span.get("scopeSpans", []):
                for span in scope_span.get("spans", []):
                    trace_id = span.get("traceId", "")
                    if not trace_id:
                        continue
                    self._merge_span(trace_id, service_name, span)

    def _merge_span(self, trace_id: str, service_name: str, span: dict) -> None:
        with self._lock:
            existing = self._traces.get(trace_id)
            if existing is None:
                existing = {
                    "trace_id":      trace_id,
                    "service_name":  service_name,
                    "status":        "UNSET",
                    "duration_ms":   0.0,
                    "span_count":    0,
                    "root_span":     "",
                    "error_message": "",
                    "error_spans":   [],
                    "started_at":    "",
                    "_arrived_at":   time.time(),
                }
                self._evict_if_full()
                self._traces[trace_id] = existing
                self._arrival_order.append(trace_id)

            existing["span_count"] += 1

            start_ns = span.get("startTimeUnixNano", 0)
            end_ns = span.get("endTimeUnixNano", 0)
            if start_ns and not existing["started_at"]:
                existing["started_at"] = _nano_to_iso(int(start_ns))
            if start_ns and end_ns:
                dur = (int(end_ns) - int(start_ns)) / 1e6
                existing["duration_ms"] = max(existing["duration_ms"], dur)

            status = span.get("status", {})
            status_code = status.get("code", 0)
            is_error = status_code == 2 or status_code == "STATUS_CODE_ERROR"
            if is_error:
                existing["status"] = "ERROR"
                name = span.get("name", "")
                msg = status.get("message", "")
                for ev in span.get("events", []):
                    for attr in ev.get("attributes", []):
                        if attr.get("key") == "exception.message":
                            msg = _str_val(attr.get("value", {})) or msg
                existing["error_spans"].append({"name": name, "error": msg})
                if not existing["root_span"]:
                    existing["root_span"] = name
                    existing["error_message"] = msg
            elif existing["status"] == "UNSET":
                existing["status"] = "OK"

    def _evict_if_full(self) -> None:
        while len(self._arrival_order) >= self._max_spans:
            oldest = self._arrival_order.popleft()
            self._traces.pop(oldest, None)


# ------------------------------------------------------------------
# Process-wide singleton
# ------------------------------------------------------------------
#
# The OTLP receiver is a *push* endpoint: spans arrive between RCA runs and must
# accumulate in a long-lived buffer. build_backend() therefore reuses one started
# receiver per (host, port) instead of creating a fresh, never-started one per run.

_shared_lock = threading.Lock()
_shared: dict[tuple[str, int], OtlpReceiver] = {}


def get_shared_receiver(
    host: str = "0.0.0.0",
    port: int = 4318,
    max_spans: int = _DEFAULT_MAX_SPANS,
) -> OtlpReceiver:
    """Return a started, process-wide OtlpReceiver for (host, port), creating it once."""
    key = (host, port)
    with _shared_lock:
        receiver = _shared.get(key)
        if receiver is None or not receiver.is_available():
            receiver = OtlpReceiver(host=host, port=port, max_spans=max_spans)
            receiver.start()
            _shared[key] = receiver
        return receiver


# ------------------------------------------------------------------
# Protobuf (default OTLP/HTTP wire format)
# ------------------------------------------------------------------

def _decode_protobuf(body: bytes) -> dict:
    """
    Decode an OTLP/HTTP protobuf ExportTraceServiceRequest into the same
    JSON-shaped dict that _ingest() consumes (camelCase keys, traceId base64,
    *UnixNano as strings, status.code as STATUS_CODE_* names).

    Requires the `opentelemetry-proto` package; raises _DecodeError on any
    failure so the handler can reply 400.
    """
    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )
    except ImportError as exc:  # optional dep missing
        raise _DecodeError("opentelemetry-proto not installed") from exc

    try:
        req = ExportTraceServiceRequest()
        req.ParseFromString(body)
        return MessageToDict(req)
    except Exception as exc:  # malformed protobuf
        raise _DecodeError(str(exc)) from exc


def _empty_protobuf_response() -> bytes:
    """Serialised empty ExportTraceServiceResponse, or `b""` if proto absent."""
    try:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceResponse,
        )
        return ExportTraceServiceResponse().SerializeToString()
    except Exception:
        return b""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _kv_to_dict(attrs: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in attrs:
        key = a.get("key", "")
        val = _str_val(a.get("value", {}))
        if key:
            out[key] = val
    return out


def _str_val(val: dict) -> str:
    if "stringValue" in val:
        return val["stringValue"]
    if "intValue" in val:
        return str(val["intValue"])
    if "boolValue" in val:
        return str(val["boolValue"]).lower()
    return str(val) if val else ""


def _nano_to_iso(nano: int) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(nano / 1e9, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _ts_in_range(iso: str, start_ts: int, end_ts: int) -> bool:
    if not iso:
        return True  # no timestamp → don't filter out
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        ts = dt.timestamp()
        return start_ts <= ts <= end_ts
    except Exception:
        return True
