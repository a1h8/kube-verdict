# Scenarios — one bank, more than 30 cases, and misdirection

## Three banks today

| Bank | Cases | Format | Tested by | Goes through |
|---|---|---|---|---|
| `cases/0NN_*` | 23 | synthetic `input.json` + `expect.json` | `tests/cases/test_case_bank.py` (offline) | graph factory + context builder only |
| `cases/helm_cases/h00N_*` | 6 | Helm-oriented | `tests/helm_cases/` | helm case factory |
| `tests/integration/cases/h0NN_*` | 18 | native (`kube/`, `helm/`, `prometheus/`, `loki/`, `otel/`) | collectors, workflow, Ollama test | the real collectors and LangGraph workflow |

Their IDs clash (`h001`–`h006` exist twice), several cover the same incident, and only
the native bank exercises the code a live run uses.

## Step 1 — one bank (native), past 30

The native format is kept, because it is the one the real collectors and the workflow
read. Legacy cases are **migrated when they add something the native bank lacks**, and
retired otherwise. A first triage, from their `notes`:

| Legacy case | Native equivalent | Decision |
|---|---|---|
| `017_dns_failure` (CoreDNS in kube-system, symptom in production) | planned h018 | migrate as h018 (needs P2, multi-namespace) |
| `013_evicted_disk_pressure` (node DiskPressure eviction) | — | migrate (needs P1, Node) |
| `019_statefulset_update_stuck` (two compounding drifts) | h011 (broken) | migrate, replaces h011 |
| `020_ingress_backend_not_found` (backend service renamed by upgrade) | — | migrate |
| `009_service_selector_mismatch` (0-endpoint service, pod healthy) | — | migrate: the cause is not on the failing pod |
| `018_secret_wrong_key` (Secret exists, wrong key) | — | migrate: "it exists" is a decoy |
| `010_readiness_probe_failure` (pool size drift → probe 5xx) | — | migrate: looks like a probe issue, is a pool size |
| `011_liveness_probe_failure` (probe port hits Redis) | h009 partly | migrate |
| `012_init_container_failure` (wrong DB_NAME in init) | h008 partly | migrate |
| `015_hpa_metrics_unavailable` (metrics-server down in kube-system) | h007 | merge into h007 |
| `004_pending_pvc`, `006_pending_insufficient_cpu`, `007_node_affinity_unmet`, `014_resource_quota_exceeded`, `016_rbac_forbidden` | h010, h005 partly | migrate the ones with a distinct cause |
| `001`, `002`, `003`, `005`, `007_prometheus_alert`, `008_*`, `009_kyverno_violation` | h001–h006, h013, h015 | retire (covered) |
| `cases/helm_cases/h001`–`h006` | h001–h004 partly | rename out of the `h0NN` space or retire |

Each migrated legacy case is rebuilt as native fixtures (real Kubernetes YAML, not
`input.json`). The legacy bank and its tests are removed once its cases are migrated or
retired. That gives about 30 native cases **before** writing a new one.

## Step 2 — the misdirection family (tier M)

What the live failures had in common: the cause produced **several side effects**, and
the loudest one pointed to a plausible wrong cause. A tier-M case has one cause, two to
four side effects across different signals, and at least one side effect that is a
convincing wrong answer. Ground truth: `root_cause_contains` for the cause,
`root_cause_must_not_contain` for each "looks like".

| Case | Looks like | Is | Side effects (signal) |
|---|---|---|---|
| M1 | memory limit too low | expired upstream certificate | TLS retries buffer requests → client OOMKilled (events); 5xx (Prometheus); `x509` only in upstream logs (Loki) |
| M2 | database capacity | feature-flag ConfigMap change disabled the cache | DB CPU alert and slow queries (Prometheus); API latency SLO burn; ConfigMap drift 15 min earlier (Helm) |
| M3 | resource starvation | NetworkPolicy change blocks egress to the DB | CrashLoop on readiness (events); HPA at max (HPA status); node CPU high from restarts (Prometheus) |
| M4 | application bug | CoreDNS throttled (ndots:5 + query storm) | intermittent timeouts in 3 services (traces); 5xx with random paths (logs); CoreDNS CPU throttling (Prometheus) |
| M5 | RBAC / auth issue | node clock skew breaks token validation | 401s on one node's pods only (logs); `token is not valid yet` (logs); NTP alert on the node (Prometheus) |
| M6 | consumer bug | Kafka PVC full | consumer lag alert; producer timeouts (traces); `No space left on device` on the broker (logs); PVC at 100 % |
| M7 | the image rollout | an unrelated node drain at the same minute | rollout and drain events interleaved; only pods on the drained node fail; new image healthy elsewhere |
| M8 | probe misconfiguration | HPA scaling on the wrong metric thrashes | readiness failures on new pods; HPA events every 30 s; latency per pod uncorrelated with load |

These need the loader prerequisites P1–P5 (`docs/test-cases.md` → tier C § Prerequisites)
and, for M2 / M7, timing (P3: ReplicaSet revision and change time).

## Gate

- [ ] Legacy triage confirmed case by case (the table above is a first read of `notes`)
- [ ] ≥ 30 native cases, legacy banks removed
- [ ] P1–P5 loader prerequisites
- [ ] M1–M4 built and in the veracity report, then M5–M8
- [ ] Every new case enters `veracity.md`'s report in the same PR

## Next step

Confirm the legacy triage (one decision per row), then migrate in the order that needs
no prerequisite first: `009_service_selector_mismatch`, `018_secret_wrong_key`,
`010_readiness_probe_failure`, `020_ingress_backend_not_found`.
