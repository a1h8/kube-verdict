# KubeVerdict ↔ PatchTST — relationship map

One picture of how the two repos divide responsibility, and where the pieces
built in this cycle (native signal webhook, h013, h014, h015, the 3 observability axes)
sit relative to each other. Detail lives in [architecture.md](architecture.md)
(kube-verdict) and PatchTST's `docs/ARCHITECTURE.md` / `docs/SIGNAL_VALIDATION.md`
— this is the map, not the territory.

```mermaid
flowchart TB
    subgraph KV["kube-verdict — RCA engine (air-gapped)"]
        direction TB
        KVG["Ontology graph<br/>K8s + Helm + drift"]
        KVW["LangGraph workflow<br/>hypothesize → analyze → verdict"]
        KVH["h-series cases<br/>h001–h012 manifest/drift · h013 Prometheus SLO · h014 Loki logs · h015 OTel"]
        KVWH["POST /api/v1/webhook/signal<br/>api/signal_mapper.py"]
        KVDJ["Decision Journey<br/>dashboard/DecisionJourney.jsx"]
        KVG --> KVW
        KVH -.fixtures for.-> KVG
        KVWH --> KVW
        KVW --> KVDJ
    end

    subgraph PT["PatchTST — temporal evidence layer"]
        direction TB
        PTD["RegimeSwitchDetector<br/>forecast (anticipation) + reconstruction (detective)"]
        PTS["scenarios/library.py<br/>h013 latency · h014 cert stall · h015 etcd compaction"]
        PTA["kubeverdict-alert sink<br/>kb/alert.py"]
        PTV["signal-capture harness<br/>tools/capture_signals.py"]
        PTDB["Validation dashboard<br/>tools/render_dashboard.py"]
        PTS --> PTD
        PTD --> PTV
        PTV --> PTDB
        PTD --> PTA
    end

    PTA -- "SignalAlert JSON<br/>(mirrors AnomalyResult)" --> KVWH

    subgraph OBS["Observability — 3 axes"]
        direction LR
        AX1["Axis 1 — Jaeger<br/>live trace backend<br/>(needs real infra)"]
        AX2["Axis 2 — Decision Journey<br/>per-session, built (B9)"]
        AX3["Axis 3 — PatchTST validation<br/>per-scenario, built this cycle"]
    end

    KVDJ -.is.-> AX2
    PTDB -.is.-> AX3
    KVH -- "otel/ fixtures today<br/>real backend later" --> AX1

    subgraph DEPLOY["Deployment targets — not yet spent"]
        direction LR
        GCP["GCP Dataflow<br/>managed, fastest to run"]
        SOV["Sovereign — Scaleway/OVH/Outscale<br/>Flink-on-K8s + S3-compatible store"]
    end

    PT -.runs on.-> DEPLOY

    classDef kv fill:#cde2fb,stroke:#2a78d6,color:#0b0b0b;
    classDef pt fill:#fcead9,stroke:#eb6834,color:#0b0b0b;
    classDef obs fill:#f0efec,stroke:#898781,color:#0b0b0b;
    classDef deploy fill:#f0efec,stroke:#898781,color:#0b0b0b,stroke-dasharray: 4 3;
    class KVG,KVW,KVH,KVWH,KVDJ kv;
    class PTD,PTS,PTA,PTV,PTDB pt;
    class AX1,AX2,AX3 obs;
    class GCP,SOV deploy;
```

## Reading it

- **kube-verdict never re-implements detection** — it receives an already
  -aggregated `SignalAlert` (severity, score, method, labels), never a raw
  time series. PatchTST never re-implements RCA — it stops at "here's an
  anomalous signal," it doesn't reason about root cause.
- **The webhook is the only coupling point.** `POST /api/v1/webhook/signal`
  (kube-verdict) ↔ `kubeverdict-alert` sink (PatchTST) — one schema
  (`SignalAlert` / `AnomalyResult`), one direction (push), best-effort (a
  failed POST never blocks the PatchTST pipeline).
- **h013, h014 and h015 sit in both repos on purpose** — PatchTST validates *can
  the detector see this pattern* (synthetic time series, offline); kube-verdict
  validates *can the RCA pipeline explain it once evidence arrives*, one signal
  each: h013 via Prometheus SLO burn-rate / p99-p95 alert fixtures on a pod
  Kubernetes reports healthy, h014 via Loki log fixtures (expired certificate,
  pods Running but not ready), h015 via OTel trace fixtures. Same scenarios, two
  different offline-first checks, no live cluster or cloud spend needed for
  either.
- **Axis 1 (Jaeger) is the one dashed line that still means real
  infrastructure** — axes 2 and 3 are both already free (local files /
  existing session store); deploying the actual observability stack (and
  picking GCP vs. a sovereign provider) is the one step in this whole map
  that isn't done by just writing more code.
