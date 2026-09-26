# PatchTST readiness — what kube-verdict actually needs from it

Companion to [kubeverdict-patchtst-map.md](kubeverdict-patchtst-map.md) (the
architecture picture) and [cloud-prerequisites.md](cloud-prerequisites.md)
(the GCP/Scaleway gate). That map describes the relationship; this is the
narrower, consumer-side question: **before wiring PatchTST into a real
GCP/Scaleway example, what does kube-verdict require from it to trust the
signal, and what does kube-verdict explicitly not care about?**

Per [[feedback_spec_before_implementation]] and
[[project_gcp_sovereign_cloud_goal]]: this gate must be green before
provisioning cloud infra for either PatchTST or kube-verdict together — a
solid scenario and a proof result kube-verdict doesn't have to second-guess,
before spending cloud budget.

## The only coupling point

`POST /api/v1/webhook/signal` (kube-verdict) ← `kubeverdict-alert` sink
(PatchTST, `kb/alert.py`). kube-verdict never calls into PatchTST; PatchTST
pushes, best-effort, one direction. kube-verdict's own webhook contract is
`SignalAlert` (`api/models.py`):

```python
entity_uid: str
metric_name: str
ts: int                # epoch milliseconds
severity: str          # normal | warning | critical
score: float
method: str            # "patchtst" | "zscore"
horizon: str = ""      # short | medium | long | ""
n_points: int = 0
labels: dict[str, str] = {}
text: str = ""
```

`api/signal_mapper.py` turns this into a query + namespace list for the RCA
pipeline — it reads `labels` for a `deployment`/`pod`/`service`/... key to
scope the investigation, and `severity` to filter out `normal` (non-anomalous)
pushes. **If PatchTST's `AnomalyResult` → `SignalAlert` mapping ever drops or
renames a resource label, kube-verdict silently loses the ability to scope
the investigation to a namespace** — this is the one field-level contract
worth a live check, not just a schema-shape check.

## What "ready" means, concretely

Verified live 2026-09-26 against the running `rancher-desktop` cluster (both
`patchtst` and `kubeverdict` namespaces already deployed there) — see
[evidence/patchtst-readiness-live.md](evidence/patchtst-readiness-live.md)
for the full run detail. Checklist below updated from that pass.

- [x] **The push actually lands.** A real `SignalAlert` POSTed from inside
      the `patchtst` namespace to kube-verdict's in-cluster webhook service
      got `HTTP 202` and a real session ID back; kube-verdict's own logs
      confirm it started a session scoped to the pushed entity.
- [x] **The resource labels survive the round-trip.** The same request's
      `labels` (`namespace`, `deployment`) round-tripped through
      `api/signal_mapper.py` into a correctly-scoped investigation — not
      skipped, not misrouted.
- [x] **Failure is actually best-effort.** Verified by code, not by killing
      the endpoint live: `KubeVerdictAlertSink.write()` (`kb/alert.py:105-111`)
      catches `Exception` around `post_alerts()`, logs, and only re-raises
      when `raise_on_error=True` is explicitly set (default `False`) —
      matches `tests/test_kubeverdict_alert.py`. No live re-test needed for
      this one; the code path is unambiguous and already unit-covered.
- [x] **The KB read/write path works on the deployment target.** The
      `patchtst-kb` MinIO bucket already holds 46+ real `.parquet` files
      from a prior successful run (2026-09-22/23) — `kb/store.py`'s `s3://`
      resolution demonstrably works against live MinIO, not just the
      `file://` unit test.
- [x] **PatchTST's own deployment target actually ran once, live.** Submitted
      `deploy/flink/50-submit-mimir.example.yaml` fresh: Beam job
      `BeamApp-root-0926123742-33712cea` went `RUNNING` → `FINISHED` on the
      real Flink cluster, 2/2 tasks finished, 0 failed, RocksDB checkpoint
      storage confirmed pointed at `s3://patchtst-flink/checkpoints`. One
      transient `FileNotFoundException` on artifact retrieval logged then
      self-recovered on retry — not a hard blocker, but worth a closer look
      if it recurs under load.
      - *Caveat, for honesty:* this specific run's zscore detector found no
        anomaly in the live `sim_.+` window, so it didn't itself write a
        *new* KB row — the KB-write proof above rests on the pre-existing
        files, not this run. The two facts are both true and both needed,
        but weren't captured as one atomic proof (a run that both executes
        cleanly *and* writes a fresh row from a genuine detected anomaly).
        Worth re-running once a real anomaly window is available.
      - The two real fixes already found on `feat/flink-local-live`
        (`00-flink-conf.yaml` blob port, `30-job-server.yaml` job-host) are
        still sitting uncommitted — commit them; today's run already depends
        on both being applied in the live cluster.

## What kube-verdict explicitly does *not* need from PatchTST

- **Model serving (Triton/ONNX)** — PatchTST's roadmap D8 leaves this
  deliberately open/in-process. kube-verdict only consumes the webhook
  output; how PatchTST's model runs internally is invisible to it and never
  blocks kube-verdict readiness.
- **Live Kafka/OTLP source validation** — PatchTST's D2 still has this
  pending. kube-verdict doesn't care which source connector fed the
  detector, only that a `SignalAlert` eventually arrives shaped correctly.
- **PatchTST's own validation dashboard** (Axis 3, `tools/render_dashboard.py`)
  — stays PatchTST's own concern, not part of kube-verdict's gate.

## Why this matters now

This is the dependency-readiness half of the same gate
`docs/cloud-prerequisites.md` §5 tracks for kube-verdict's own observability
backends: a GCP/Scaleway PatchTST example is only worth provisioning once the
signal it would send is proven, not assumed. Get a green here first — a
solid scenario, one clean live capture, no hesitation on the result — then
provision the cloud infra for both repos together.
