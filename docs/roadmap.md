# Roadmap

Blocs are grouped into maturity phases on the live dashboard — **Foundation** (B1–B5),
**Decision Engine** (B6), **Distribution & Skills** (B7–B8), **Deep Observability** (B9–B10),
**Production Hardening** (B11), **Common Interface** (B12), **Real-world validation** (B13), and **Next / Frontier** (B14). Status is computed on
each deploy by `tools/roadmap.py` from deterministic file/tag checks — not a self-graded
score. A check stays red until the thing it verifies actually exists (e.g. B7's release check
only turns green once a `v*` tag is pushed and the image is really built, not just because the
workflow file is present).

Section order below follows the bloc numbering (ascending); forward-looking work is collected
under Next.

## Done

> **What `[x]` means here:** the capability is implemented and wired into the
> pipeline and covered by offline/fixture tests. The observability collectors
> (Prometheus, OTel, Loki, PatchTST) are wired but **not yet validated against a
> live endpoint with real data** — that validation is tracked as open items
> below (see *"Prometheus wired to real data"* and *Loki Full Integration (B10)*).
> This matches the README: the primary **validated** inputs are Kubernetes
> events and Helm drift.

- [x] **Evidence-first hypothesis generation** — ontology causal chains + anchor violations + RemediationEngine rules + KB examples → hypotheses before LLM; LLM only fills remaining slots (P(token | top-k context))
- [x] **Beam search confidence routing** — `path_confidence_history` detects LOW × 2 → early path switch; `archive_path_node` re-ranks remaining candidates via hybrid_search on failed analysis text
- [x] **RRF-on-anchors** — `index_anchor_violations()` indexes each manifest drift field as `doc_source="anchor"` (×1.6 weight); Phase 2b processes `anchor:` UIDs from hybrid_search into testable hypotheses
- [x] **FastAPI REST API** — `POST /run`, `GET /state`, `GET /stream` (SSE), `POST /feedback`, `DELETE`; exposes `hypothesis_sources`, `path_confidence_history`, `edge_log` with `declining` flag
- [x] **SQLite persistence** — sessions + LangGraph checkpoints survive restarts; FAISS index preloaded from `index.faiss` at startup (Option A) or rebuilt from raw texts in `vector_store_docs` without re-collecting from the cluster (Option B); SQL dialect compatible with PostgreSQL (`ON CONFLICT DO UPDATE`)
- [x] **LangGraph multi-path workflow** — hypothesize → analyze → retry / archive_path → select_best → dry_run → human_review
- [x] **Alertmanager webhook** — receives Prometheus Alertmanager `POST /webhook` payloads; auto-triggers RCA session; maps alert `labels` to namespace + resource + query (`demo/demo_webhook.py`)
- [x] **AnchorEngine** — manifest + schema anchors; `anchor_fix_hints()` generates `helm upgrade --set` commands; **anchor pivot table** in UI (declared → observed → status → fix)
- [x] **BM25 + FAISS hybrid retrieval** — K8s-aware BM25 tokeniser + FAISS dense cosine + Reciprocal Rank Fusion; `retrieval_stats` (dense/sparse/fused/top_rrf_score) in UI
- [x] **Integration test cases — native format** — `tests/integration/cases/` with real K8s YAML (pod, events, values.yaml, helmfile, PolicyReport); unified `case_loader.py`
- [x] **Deployment readiness detection** — `_detect_missing_deps()` scans pod specs for all resource references (secrets, configmaps, PVCs, imagePullSecrets, serviceaccounts, RBAC, NetworkPolicy egress); generates `missing.*` / `netpol.*` annotations; `anchor_fix_hints()` produces concrete `kubectl create/edit` commands; h004/h005/h006 cases cover the full range
- [x] **Pipeline trace UI** — 10-step pre-LLM pipeline visualization (auto-runs on case select, no Ollama needed); Step 10 proposes values.yaml diffs + helm commands + OPA/Kyverno fixes
- [x] **RemediationEngine** — rule-based weighted hypotheses for LOW-confidence fallback; integrated in pipeline trace Bonus step
- [x] **OPA / Kyverno policy integration** — `PolicyCollector` ingests `PolicyReport` / `ClusterPolicyReport`; violations as `HAS_POLICY_VIOLATION` edges; confidence boost; fix hints
- [x] **Helm / Helmfile KB tab** — upload/paste `values.yaml`, `helmfile.yaml`, `.tgz` archives; indexed as `source=helm` documents in FAISS
- [x] **GitOps diff** — `helm template` rendered manifests vs live cluster; `LocalGitProvider` + `GithubProvider`
- [x] **Enterprise Knowledge Base** — DocStore + DocIndexer; runbooks, SOPs, Confluence, Helm charts indexed into FAISS
- [x] **Versioned K8s docs** — fetch & index official K8s docs at detected cluster version
- [x] **PatchTST signals** — multi-horizon anomaly detection on real Prometheus time series (1h/24h/7d)
- [x] **Prometheus alert correlation** — firing alerts ingested; label-matched to K8s entities; `[CRITICAL]` context section
- [x] **OTel traces** — error spans from Tempo/Jaeger; `HAS_TRACE` edges; `[TRACES]` context section
- [x] **Loki logs** — pod logs via LogQL; log level + trace IDs; `[LOGS]` context section
- [x] **Metrics server** — live CPU/memory from `metrics.k8s.io/v1beta1`; seeds PatchTST
- [x] **Pre-LLM confidence scoring** — `compute_confidence()` weights BFS, Jaccard, TF-IDF, anchors, signals, policy violations into 0–1 score
- [x] **Source weights** — per-source score multipliers; configurable via `SOURCE_WEIGHT_*` env vars

## Agent Skills / MCP (B8)

- [x] **MCP server** — expose `kube-rca`, `helm-drift-detector`, `blast-radius-estimator` as MCP tools; any agent (Claude, Cursor, Codex) can invoke them without deploying the full stack
- [x] **SKILL.md** — Claude Code skill definition; invocable from any repo with `kube-rca` skill
- [x] **OpenAPI tool schema** — OpenAI function-calling compatible; compatible with third-party agent frameworks
- [x] **Integration guide** — Cursor / Claude Desktop quickstart documented

## Decision Introspection UI (B9)

The beam-search engine already records every routing decision (`edge_log`), every archived hypothesis (`reasoning_history`), and every collector failure (`ingestion_stats`). B9 makes all of this visible — surfaced by the React **Decision Journey** view (`dashboard/`, `#/journey`) consuming the API.

- [x] **API: expose reasoning + verdict in `/state`** — `GET /sessions/{id}/state` returns `reasoning_history`, `edge_log`, and the policy `verdict` / `verdict_reasons`, so a consumer renders the decision without backend changes
- [x] **Edge-log timeline** — chronological list of `edge_log` events: router name, edge taken (`retry` / `next_path` / `review`), reason text, and the confidence/score snapshot (Decision Journey `Timeline`)
- [x] **Eliminated-paths panel** — each `reasoning_history` entry: hypothesis, confidence, retries before elimination, summary — chosen ✓ vs eliminated ✕ (Decision Journey `Paths`)
- [x] **Fallback-status overlay** — per-collector badge row (ingest / prometheus / metrics / otel / gitops / anchor / signals): green OK or red FALLBACK with the error message as tooltip; surfaces exactly `ingestion_stats[*].fallback + error` (`FallbackStatus` in `dashboard/src/DecisionJourney.jsx`)
- [x] **Beam-search tree** — SVG dag: active path in blue, archived branches in gray, edges labeled with confidence score; node size proportional to retry count; eliminated leaves marked with an ✕ and the elimination reason on hover (`BeamTree` in `dashboard/src/DecisionJourney.jsx`)
- [x] **Live SSE refresh** — introspection panel subscribes to the existing `/stream` endpoint and re-renders each section as new `edge_log` entries or `reasoning_history` entries arrive, giving operators real-time visibility during a running session (`IntrospectionPanel` `EventSource` in `dashboard/src/DecisionJourney.jsx`)

## Loki Full Integration (B10)

Current state: basic LogQL range query against unhealthy pods, `LokiLog` nodes wired via `HAS_LOG` edges, level keyword detection, trace ID regex. The following extensions are needed.

- [ ] **Structured log parsing** — JSON-formatted log lines are parsed into key-value annotations on `LokiLog` nodes (http_status, method, path, latency, user, service); enriches hypothesis context beyond raw line text
- [ ] **Error clustering** — group identical or near-identical error messages (edit distance + embedding cosine) into a single `LogCluster` node; prevents token explosion in the LLM context and surfaces recurring patterns instead of N duplicate lines
- [ ] **Multi-tenant support** — pass `X-Scope-OrgID` header; configurable via `LOKI_ORG_ID` env var; required for shared Loki deployments (Grafana Cloud, enterprise tenants)
- [ ] **LogQL streaming (tail)** — during a live session, tail logs via `/loki/api/v1/tail` WebSocket; new error lines arrive as SSE events and are added to the graph without a full re-collect
- [ ] **Loki alert rule ingestion** — fetch active Loki ruler alerts via `/loki/api/v1/rules`; correlate firing rules with current pod entities; add `HAS_LOG_ALERT` edge with rule name and severity
- [ ] **Dashboard Loki tab** — in the pipeline trace UI, show log lines with level badge (error / warn / info), ISO timestamp, pod name, and `trace_id` hyperlinked to the OTel span view
- [ ] **Integration test case (log-first RCA)** — scenario where the root cause is detected purely from log patterns (e.g. Java heap OOM in logs → OOMKilled) with no Prometheus signal; validates the Loki → hypothesis path end-to-end

## Production Hardening (B11)

What separates a validated prototype from a prod-grade deployment. Each item is a deterministic
check that turns green when implemented.

- [x] **Shared-secret bearer gate (interim)** — `KUBEVERDICT_API_TOKEN` guards the mutating session, webhook and `/investigate` routes (`api/auth.py`, constant-time compare); no-op when unset. This is *not* per-identity auth.
- [x] **API auth — JWT / OIDC** — per-identity bearer auth on the session, webhook and `/investigate` routes; JWTs verified against the provider JWKS (RS256) with optional issuer/audience checks (`api/oidc.py`). Layers over the shared secret; `OIDC_REQUIRED=1` rejects any request without a valid JWT.
- [x] **Golden-scenario regression guard** — `tests/golden/` replays the h001–h010 decision fixtures and diffs verdict + blast-radius risk against a recorded `baseline.json`; CI fails on any drift (regenerate intentionally via `python -m tests.golden.update_baseline`)
- [x] **Artifact Hub listing** — `helm/kube-verdict/artifacthub-repo.yml` + Chart.yaml `artifacthub.io/*` annotations so the chart is discoverable / verifiable
- [x] **RBAC-aware scoping** — `K8sCollector` impersonates a tenant identity via apiserver `Impersonate-User`/`-Group` headers (`KUBE_IMPERSONATE_USER` / `_GROUPS`); analysis is scoped by the tenant's RBAC, not the collector's
- [x] **Secret management** — `api/secrets.py` resolves sensitive values env → file-mounted secret → Vault KV v2 (`VAULT_ADDR`/`VAULT_TOKEN`/`VAULT_KV_PATH`); Helm `ExternalSecret` template + `existingSecret` so no plaintext kubeconfig / API keys live in values

## Common Interface (B12)

One canonical verdict shared by every consumer — so the contract is visible and tracked, not
rediscovered by reading git history.

- [x] **Canonical verdict model frozen** — `IncidentReport` (`decision/models.py`) + formal `BlastRadius` / `RollbackPlan`, locked by `tests/unit/test_decision_models.py`
- [x] **Single investigation pipeline** — MCP `kube_rca` routes through `services.investigation_service` (the same graph as the REST API), not a parallel path
- [x] **IDP integration contract published** — `docs/idp-contract.md` documents the verdict envelope for portal / SRE / agent consumers; realized by `api/verdict_contract.py` (`VerdictEnvelope`) and `POST /api/v1/investigate`

## Real-world validation (B13)

The credibility jump: move from synthetic fixtures (h001–h010) to real captured incidents.
Each milestone is a recorded artifact that flips a deterministic check — cadence you can see,
not motivation. Sequence matters: each unlocks the next.

Captured by `tools/b13_capture.py` against a live k3d/k3s cluster: deploy the scenario manifest →
wait for the **real** failure state → run the canonical investigation pipeline live (real
`K8sCollector` + Prometheus via the apiserver service-proxy) → freeze the verdict. These are
**captured snapshots, not deterministic CI baselines** — a live LLM + Monte-Carlo verdict varies
run to run, so `real_00N.json` is provenance evidence and is *not* wired into the B11 regression
guard. See `docs/evidence/prometheus-live.md`.

- [x] **Prometheus wired to real data** — live run captured against the real `kube-prometheus-stack` endpoint (`fallback=False`); evidence in `docs/evidence/prometheus-live.md`
- [x] **First real incident captured end-to-end** — h001 crashloop on a live cluster → real LLM root cause + verdict frozen in `tests/golden/real_001.json` (the first verdict not from synthetic data)
- [x] **Second real incident captured** — h002 ImagePullBackOff captured live in `tests/golden/real_002.json` (snapshot; the B11 regression baseline stays on the synthetic h001–h010 set, since a live verdict is non-deterministic)

## Next / Frontier (B14)

Forward-looking backlog — now tracked by `tools/roadmap.py` so the frontier is visible in the
dashboard count instead of invisible prose. Every item below is TODO by design; each turns green
only when the capability actually lands.

- [ ] **Evidence Lineage** — dedup raw signals by Kubernetes owner + error family + time window; build lineage graph (evidence → root cause → remediation nodes/edges); expose ranked reasoning paths with alternatives in API and UI
- [ ] **Monte Carlo Tree Search** — replace greedy beam search with MCTS: UCB1 node selection, rollout via LLM, backpropagation of confidence scores across hypothesis tree
- [ ] **More h-series cases** — h012+: network latency, cert expiry, etcd compaction, …
- [ ] **Helmfile multi-release** — case with `helmfile.yaml` covering interdependent releases
- [ ] **Multi-cluster support** — analyse multiple contexts in one session
- [ ] **Alertmanager webhook (production)** — auth, dedup, grouping, silences, multi-tenant routing; hardened for real Alertmanager deployments
- [ ] **Slack / PagerDuty enrichment** — push RCA summary via webhook
