# Roadmap

## Done

- [x] **Evidence-first hypothesis generation** — ontology causal chains + anchor violations + RemediationEngine rules + KB examples → hypotheses before LLM; LLM only fills remaining slots (P(token | top-k context))
- [x] **Beam search confidence routing** — `path_confidence_history` detects LOW × 2 → early path switch; `archive_path_node` re-ranks remaining candidates via hybrid_search on failed analysis text
- [x] **RRF-on-anchors** — `index_anchor_violations()` indexes each manifest drift field as `doc_source="anchor"` (×1.6 weight); Phase 2b processes `anchor:` UIDs from hybrid_search into testable hypotheses
- [x] **FastAPI REST API** — `POST /run`, `GET /state`, `GET /stream` (SSE), `POST /feedback`, `DELETE`; exposes `hypothesis_sources`, `path_confidence_history`, `edge_log` with `declining` flag
- [x] **LLM pluggable** — `LLMClient` abstract interface; `OllamaClient` (local), `OpenAIClient`, `AnthropicClient`; selected via `LLM_PROVIDER` env var; zero code change to swap provider
- [x] **SQLite persistence** — sessions + LangGraph checkpoints survive restarts; FAISS index preloaded from `index.faiss` at startup (Option A) or rebuilt from raw texts in `vector_store_docs` without re-collecting from the cluster (Option B); SQL dialect compatible with PostgreSQL (`ON CONFLICT DO UPDATE`)
- [x] **LangGraph multi-path workflow** — hypothesize → analyze → retry / archive_path → select_best → dry_run → human_review
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

## Next

- [ ] **Monte Carlo Tree Search** — replace greedy beam search with MCTS: UCB1 node selection, rollout via LLM, backpropagation of confidence scores across hypothesis tree
- [ ] **More h-series cases** — h012+: network latency, cert expiry, etcd compaction, …
- [ ] **Helmfile multi-release** — case with `helmfile.yaml` covering interdependent releases
- [ ] **Multi-cluster support** — analyse multiple contexts in one session
- [ ] **Slack / PagerDuty enrichment** — push RCA summary via webhook
- [ ] **RBAC-aware scoping** — per-namespace analysis with service-account impersonation
