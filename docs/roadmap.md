# Roadmap

## Done

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

- [ ] **More h-series cases** — h007 PVC not bound, h008 Kyverno violation at admission, h009 resource quota exceeded, …
- [ ] **Helmfile multi-release** — h00N case with `helmfile.yaml` covering multiple interdependent releases
- [ ] **Multi-cluster support** — analyse multiple contexts in one session
- [ ] **Slack / PagerDuty enrichment** — push RCA summary via webhook
- [ ] **RBAC-aware scoping** — per-namespace analysis with service-account impersonation
