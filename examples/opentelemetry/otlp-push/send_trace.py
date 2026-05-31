#!/usr/bin/env python3
"""
Push sample traces to KubeVerdict's OTLP/HTTP receiver.

Sends one OK trace and one ERROR trace using the default OTLP protobuf wire
format over plain urllib — the only dependency is `opentelemetry-proto`, which
KubeVerdict already requires. No OTel SDK or running cluster needed.

Usage:
    python send_trace.py                                  # http://localhost:4318
    python send_trace.py --endpoint http://host:4318
    python send_trace.py --service payment --json         # use OTLP/JSON instead
    python send_trace.py --gzip                            # gzip the request body
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import urllib.request

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)


def _build_protobuf(service: str) -> bytes:
    now_ns = time.time_ns()
    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    rs.resource.attributes.add(key="service.name").value.string_value = service

    spans = rs.scope_spans.add()

    ok = spans.spans.add()
    ok.trace_id = b"\x01" * 16
    ok.span_id = b"\x01" * 8
    ok.name = "GET /healthz"
    ok.start_time_unix_nano = now_ns
    ok.end_time_unix_nano = now_ns + 12_000_000  # 12 ms

    err = spans.spans.add()
    err.trace_id = b"\x02" * 16
    err.span_id = b"\x02" * 8
    err.name = "POST /checkout"
    err.start_time_unix_nano = now_ns
    err.end_time_unix_nano = now_ns + 940_000_000  # 940 ms
    err.status.code = 2  # STATUS_CODE_ERROR
    err.status.message = "upstream payment-svc timed out after 900ms"
    ev = err.events.add()
    ev.name = "exception"
    ev.attributes.add(key="exception.message").value.string_value = (
        "ConnectTimeout: payment-svc:8080"
    )
    return req.SerializeToString()


def _build_json(service: str) -> bytes:
    from google.protobuf.json_format import MessageToDict

    req = ExportTraceServiceRequest()
    req.ParseFromString(_build_protobuf(service))
    return json.dumps(MessageToDict(req)).encode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", default="http://localhost:4318",
                    help="OTLP/HTTP base URL (default: http://localhost:4318)")
    ap.add_argument("--service", default="checkout", help="service.name to report")
    ap.add_argument("--json", action="store_true", help="send OTLP/JSON instead of protobuf")
    ap.add_argument("--gzip", action="store_true", help="gzip-compress the request body")
    args = ap.parse_args()

    url = args.endpoint.rstrip("/") + "/v1/traces"
    if args.json:
        body, content_type = _build_json(args.service), "application/json"
    else:
        body, content_type = _build_protobuf(args.service), "application/x-protobuf"

    headers = {"Content-Type": content_type}
    if args.gzip:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"

    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"pushed 2 trace(s) → {url}  (HTTP {resp.status})")
            return 0
    except urllib.error.URLError as exc:
        print(f"failed to reach {url}: {exc}", file=sys.stderr)
        print("is KubeVerdict running with OTEL_BACKEND_TYPE=otlp?", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
