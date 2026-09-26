# GCP & Scaleway — deployment prerequisites

Checklist to complete **before provisioning** real infra for kube-verdict — not a
deployment guide (see `docs/deployment.md` for that once infra exists). Target: each
cloud completable in **half a day**, reusing the existing Helm chart
(`helm/kube-verdict/`) and community charts for the observability backends — no
custom infra code beyond `values.yaml` overrides per cloud.

This is the "one step in the whole map that isn't done by just writing more code" —
see Axis 1 in `docs/kubeverdict-patchtst-map.md` and B15 in `docs/roadmap.md`.

---

## 1. Accounts & access

**GCP**
- [ ] Project created, billing enabled
- [ ] `gcloud` CLI authenticated (`gcloud auth login` + `gcloud config set project`)
- [ ] IAM roles: GKE Admin, Monitoring Admin (if using Cloud Monitoring alongside Grafana)

**Scaleway**
- [ ] Account + API key (access key / secret key)
- [ ] `scw` CLI configured (`scw init`)
- [ ] Kubernetes Kapsule enabled on the account

## 2. Cluster choice

- GCP: GKE Autopilot (fastest to stand up, managed node pools) — avoid standard GKE unless node-level control is actually needed
- Scaleway: Kapsule (managed K8s)
- Both: one small node pool is enough for the demo scale (h013/h014/h015 + kube-verdict + Grafana + observability backends)

## 3. Observability backends — must be real, not fixtures

The h013/h014/h015 cases are currently validated against frozen fixtures
(`tests/integration/cases/h01{3,4,5}_*/`) through the real collector code paths
(`PrometheusCollector`, `LokiSource`, `OtelCollector`). Before claiming a demonstrable
deployment, each must be reproduced against **live** backends on **both** clouds:

- [ ] Prometheus — `kube-prometheus-stack` via Helm (h013 SLO burn-rate)
- [ ] Loki — `loki-stack` via Helm (h014 cert expiry logs)
- [ ] OTel Collector — exporting to Tempo (or `OTEL_BACKEND_TYPE=otlp` against kube-verdict's own OTLP receiver) (h015 traces)
- [ ] Grafana — one instance reading Prometheus + Loki + Tempo, plus the OTLP metrics/traces kube-verdict exports about itself (B15 Axis 1 self-observability dashboard)

> **Local dress rehearsal done.** All four wirings above were proven against
> real Tempo + Loki on the local cluster first (zero cloud cost) — see
> `docs/local-observability-stack.md` (plan) and
> `docs/evidence/tempo-loki-live.md` (results: real trace persisted through
> the OTel Collector, real pod logs reaching Loki with the exact labels
> `LokiSource` queries for, Grafana reading all three sources). The boxes
> above stay unchecked because they specifically mean *on GCP/Scaleway* —
> same `demo/observability/*-values.yaml` files, only the storage backend
> changes (local/filesystem → GCS/S3-compatible). Not yet done locally: a
> live `h014`/`h015` incident reproduction and capture (tracked in
> `docs/local-observability-stack.md`'s "out of scope" section).

Config surface is already there (`config.py`): `PROMETHEUS_URL`, `LOKI_URL`,
`OTEL_BACKEND_TYPE` / `OTEL_BACKEND_URL` / `OTEL_TOKEN`. No code change needed to point
kube-verdict at real endpoints — only Helm values overrides.

## 4. Network & secrets

- [ ] Ingress/LoadBalancer exposure for the kube-verdict API and Grafana
- [ ] TLS via cert-manager + a real ACME issuer that actually succeeds (the inverse of
      the h014 scenario — verify the issuer registers and renews, don't ship the same
      failure mode you're demoing)
- [ ] Secrets (self-hosted Ollama endpoint, `KUBEVERDICT_API_TOKEN`, Grafana admin
      credentials) via the existing `api/secrets.py` resolution chain (env → mounted
      secret → Vault) or Helm `ExternalSecret` — no plaintext values
- [ ] Ollama reachable in-cluster or on cloud-local compute (`OLLAMA_URL` in
      `config.py`) — **self-hosted only, by design.** There is no external LLM API
      key anywhere in the codebase (`config.py`, `api/secrets.py`): inference stays on
      infra we operate, on both GCP and Scaleway, so no incident data ever leaves the
      cluster/cloud boundary and there's no third-party API cost or dependency to
      account for. Keep it this way — don't add an API-key LLM backend later as a
      "faster" shortcut.

## 5. Validation gate — before calling it "demonstrable"

- [ ] h013 reproduced against real Prometheus on GCP
- [ ] h013 reproduced against real Prometheus on Scaleway
- [ ] h014 reproduced against real Loki on GCP
- [ ] h014 reproduced against real Loki on Scaleway
- [ ] h015 reproduced against a real OTel backend on GCP
- [ ] h015 reproduced against a real OTel backend on Scaleway
- [ ] Grafana/OTLP self-observability dashboard (B15) shows live, refreshing data on both
- [ ] Screen-capture demo recorded running live on real infra (reuse the tooling in
      `docs/demo-recording-runbook.md`) — proof it runs for real, not just on fixtures

## 6. PatchTST dependency readiness

If the GCP/Scaleway example includes PatchTST (`DEPLOY` box in
`docs/kubeverdict-patchtst-map.md` — GCP Dataflow / sovereign Flink-on-K8s):
see [patchtst-readiness.md](patchtst-readiness.md) first. Don't provision
cloud infra for a signal source kube-verdict hasn't verified it can actually
consume.

## 7. Cost & teardown

- [ ] Note the approximate hourly cost for each cloud's cluster + observability stack
- [ ] Teardown commands documented and tested (`terraform destroy` / `gcloud container clusters delete` / `scw k8s cluster delete`) so demo infra isn't left running between sessions
