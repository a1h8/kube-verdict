# OTLP push receiver — end-to-end example

In **push mode** KubeVerdict runs its own OTLP/HTTP receiver on port `4318`
(the standard OTLP/HTTP port). Instead of querying Tempo or Jaeger, your apps —
or an OpenTelemetry Collector — push spans directly to KubeVerdict. Error traces
are buffered in memory and correlated to unhealthy pods during RCA.

The receiver accepts both wire formats of OTLP/HTTP:

| Content-Type                 | Notes                                   |
|------------------------------|-----------------------------------------|
| `application/x-protobuf`     | **Default** for OTel SDKs & Collector   |
| `application/json`           | OTLP/JSON                               |

`Content-Encoding: gzip` request bodies are decompressed automatically.

---

## 1. Enable push mode

Add to your `.env` (see [`env.snippet`](./env.snippet)):

```bash
OTEL_ENABLED=true
OTEL_BACKEND_TYPE=otlp     # tempo | jaeger | otlp
OTLP_HOST=0.0.0.0
OTLP_PORT=4318
OTLP_MAX_TRACES=2000       # in-memory ring buffer size (traces kept)
```

Start KubeVerdict normally (API or UI). The receiver is a process-wide singleton:
it starts on the first RCA run and keeps buffering pushed spans between runs.

> The buffer is **in-memory and bounded** — the oldest traces are evicted once
> `OTLP_MAX_TRACES` is reached, and everything is lost on restart. This is by
> design: KubeVerdict only needs a recent window of error traces, not durable
> storage. Use Tempo mode if you need history.

---

## 2a. Push from a demo script (no cluster needed)

[`send_trace.py`](./send_trace.py) sends one OK trace and one ERROR trace using
the default protobuf format, over plain `urllib` (only `opentelemetry-proto`
required — already a KubeVerdict dependency):

```bash
python examples/opentelemetry/otlp-push/send_trace.py --endpoint http://localhost:4318
# pushed 2 trace(s) → http://localhost:4318/v1/traces  (HTTP 200)
```

Run an RCA afterward and the ERROR trace shows up in the `[TRACES]` context
section for the matching service.

## 2b. Push from a real OTel SDK

Point any OTel SDK at the receiver — no code change beyond the exporter endpoint:

```bash
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://kube-verdict.kube-verdict.svc:4318/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf   # default; http/json also works
```

## 2c. Push via an OpenTelemetry Collector

Use [`otelcol-export.yaml`](./otelcol-export.yaml) to fan your existing traces
into KubeVerdict (you can keep exporting to Tempo at the same time):

```yaml
exporters:
  otlphttp/kubeverdict:
    traces_endpoint: http://kube-verdict.kube-verdict.svc:4318/v1/traces
    # encoding defaults to proto; the receiver also accepts json
```

---

## 3. Verify

```bash
# OK response is an (empty) ExportTraceServiceResponse:
curl -i -X POST http://localhost:4318/v1/traces \
  -H 'Content-Type: application/json' --data '{"resourceSpans":[]}'
# → HTTP/1.0 200 OK
```

Unknown paths return `404`; malformed protobuf/JSON or bad gzip return `400`.
