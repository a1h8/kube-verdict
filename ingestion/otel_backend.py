"""
OTel trace backend abstraction.

OtelBackend(ABC)
  ├── TempoBackend   — Grafana Tempo  GET /api/search + /api/traces/{id}
  └── JaegerBackend  — Jaeger         GET /api/services + /api/traces

Both backends return a normalised list of trace dicts:
  {
    "trace_id":     str,
    "service_name": str,
    "status":       "OK" | "ERROR" | "UNSET",
    "duration_ms":  float,
    "span_count":   int,
    "root_span":    str,          # operation name of root/error span
    "error_message": str,
    "error_spans":  list[dict],   # [{name, error}]
    "started_at":   str,          # ISO-8601
  }
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import requests

log = logging.getLogger(__name__)


class OtelBackend(ABC):
    """Common interface for OpenTelemetry-compatible trace backends."""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @abstractmethod
    def search_error_traces(
        self,
        service: str,
        namespace: str,
        start_ts: int,
        end_ts: int,
        limit: int = 20,
    ) -> list[dict]:
        """
        Return normalised error traces for the given service within the
        time range [start_ts, end_ts] (Unix seconds).
        """

    @abstractmethod
    def get_trace(self, trace_id: str) -> dict | None:
        """Return a single normalised trace by ID, or None if not found."""

    def is_available(self) -> bool:
        """Return True if the backend responds to a health probe."""
        try:
            resp = requests.get(
                f"{self.url}/ready",
                headers=self._headers(),
                timeout=self.timeout,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        try:
            resp = requests.get(
                f"{self.url}{path}",
                params=params or {},
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout:
            log.warning("otel: request timed out: %s", path)
            return None
        except requests.RequestException as exc:
            log.warning("otel: request failed (%s): %s", exc, path)
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Tempo backend
# ─────────────────────────────────────────────────────────────────────────────

class TempoBackend(OtelBackend):
    """
    Grafana Tempo backend.

    Search: GET /api/search?tags=...&start=...&end=...&limit=...
    Fetch:  GET /api/traces/{traceId}
    """

    def search_error_traces(
        self,
        service: str,
        namespace: str,
        start_ts: int,
        end_ts: int,
        limit: int = 20,
    ) -> list[dict]:
        params = {
            "tags": f"service.name={service} status.code=STATUS_CODE_ERROR",
            "start": start_ts,
            "end":   end_ts,
            "limit": limit,
        }
        data = self._get("/api/search", params)
        if not data:
            return []
        traces = []
        for hit in data.get("traces", []):
            tid = hit.get("traceID", "")
            if not tid:
                continue
            full = self.get_trace(tid)
            if full:
                traces.append(full)
        log.debug("tempo: found %d error traces for service=%s", len(traces), service)
        return traces

    def get_trace(self, trace_id: str) -> dict | None:
        data = self._get(f"/api/traces/{trace_id}")
        if not data:
            return None
        return _normalise_tempo_trace(data)


# ─────────────────────────────────────────────────────────────────────────────
# Jaeger backend
# ─────────────────────────────────────────────────────────────────────────────

class JaegerBackend(OtelBackend):
    """
    Jaeger backend.

    Search: GET /api/traces?service=...&tags=error%3Dtrue&start=...&end=...
    Fetch:  GET /api/traces/{traceId}
    """

    def is_available(self) -> bool:
        try:
            resp = requests.get(
                f"{self.url}/api/services",
                headers=self._headers(),
                timeout=self.timeout,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def search_error_traces(
        self,
        service: str,
        namespace: str,
        start_ts: int,
        end_ts: int,
        limit: int = 20,
    ) -> list[dict]:
        params = {
            "service": service,
            "tags":    "error=true",
            "start":   start_ts * 1_000_000,   # Jaeger uses microseconds
            "end":     end_ts   * 1_000_000,
            "limit":   limit,
        }
        data = self._get("/api/traces", params)
        if not data:
            return []
        traces = [
            _normalise_jaeger_trace(t)
            for t in data.get("data", [])
            if t
        ]
        log.debug("jaeger: found %d error traces for service=%s", len(traces), service)
        return traces

    def get_trace(self, trace_id: str) -> dict | None:
        data = self._get(f"/api/traces/{trace_id}")
        if not data or not data.get("data"):
            return None
        return _normalise_jaeger_trace(data["data"][0])


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_tempo_trace(data: dict) -> dict:
    """Convert Tempo trace JSON → normalised dict."""
    batches = data.get("batches", [])
    error_spans: list[dict] = []
    root_span_name = ""
    error_message = ""
    service_name = ""
    started_at = ""
    total_spans = 0
    duration_ms = 0.0

    for batch in batches:
        resource_attrs = {
            a["key"]: a.get("value", {}).get("stringValue", "")
            for a in batch.get("resource", {}).get("attributes", [])
        }
        svc = resource_attrs.get("service.name", "")
        if svc and not service_name:
            service_name = svc

        for scope in batch.get("scopeSpans", []):
            for span in scope.get("spans", []):
                total_spans += 1
                if not started_at and span.get("startTimeUnixNano"):
                    started_at = _nano_to_iso(span["startTimeUnixNano"])
                if span.get("endTimeUnixNano") and span.get("startTimeUnixNano"):
                    dur = (int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"])) / 1e6
                    duration_ms = max(duration_ms, dur)

                status = span.get("status", {})
                if status.get("code") == 2 or status.get("code") == "STATUS_CODE_ERROR":
                    name = span.get("name", "")
                    msg = status.get("message", "")
                    # Check events for exception details
                    for ev in span.get("events", []):
                        for attr in ev.get("attributes", []):
                            if attr["key"] == "exception.message":
                                msg = attr.get("value", {}).get("stringValue", msg)
                    error_spans.append({"name": name, "error": msg})
                    if not root_span_name:
                        root_span_name = name
                        error_message = msg

    return {
        "trace_id":      data.get("traceID", ""),
        "service_name":  service_name,
        "status":        "ERROR" if error_spans else "OK",
        "duration_ms":   duration_ms,
        "span_count":    total_spans,
        "root_span":     root_span_name,
        "error_message": error_message,
        "error_spans":   error_spans,
        "started_at":    started_at,
    }


def _normalise_jaeger_trace(data: dict) -> dict:
    """Convert Jaeger trace JSON → normalised dict."""
    spans = data.get("spans", [])
    processes = data.get("processes", {})
    error_spans: list[dict] = []
    service_name = ""
    started_at = ""
    duration_ms = 0.0

    if spans:
        root = spans[0]
        pid = root.get("processID", "")
        service_name = processes.get(pid, {}).get("serviceName", "")
        started_at = _micro_to_iso(root.get("startTime", 0))
        duration_ms = root.get("duration", 0) / 1000.0   # μs → ms

    for span in spans:
        is_error = any(
            t.get("key") == "error" and t.get("value") is True
            for t in span.get("tags", [])
        )
        if is_error:
            name = span.get("operationName", "")
            msg = next(
                (t["value"] for t in span.get("tags", []) if t.get("key") == "error.message"),
                ""
            )
            error_spans.append({"name": name, "error": str(msg)})

    return {
        "trace_id":      data.get("traceID", ""),
        "service_name":  service_name,
        "status":        "ERROR" if error_spans else "OK",
        "duration_ms":   duration_ms,
        "span_count":    len(spans),
        "root_span":     error_spans[0]["name"] if error_spans else "",
        "error_message": error_spans[0]["error"] if error_spans else "",
        "error_spans":   error_spans,
        "started_at":    started_at,
    }


def _nano_to_iso(nano: int | str) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(nano) / 1e9, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _micro_to_iso(micro: int) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(micro / 1e6, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def build_backend(
    backend_type: str,
    url: str,
    token: str | None = None,
    timeout: int = 30,
    otlp_host: str = "0.0.0.0",
    otlp_port: int = 4318,
    otlp_max_spans: int = 2_000,
) -> OtelBackend:
    """Factory — returns the right backend from config."""
    if backend_type.lower() == "jaeger":
        return JaegerBackend(url=url, token=token, timeout=timeout)
    if backend_type.lower() == "otlp":
        # Push model: the receiver must outlive a single RCA run so spans pushed
        # between runs accumulate. Return a process-wide singleton, started once.
        from ingestion.otlp_receiver import get_shared_receiver
        return get_shared_receiver(host=otlp_host, port=otlp_port, max_spans=otlp_max_spans)
    return TempoBackend(url=url, token=token, timeout=timeout)
