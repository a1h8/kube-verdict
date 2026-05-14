# Streamlit UI

```
streamlit run ui/app.py
```

## Tab 1 — Root Cause Analysis

- **Sidebar**: kube context selector, namespace, optional collectors (Metrics server, GitOps drift, Prometheus, OTel/Loki)
- **Pipeline bar**: 8-step progress — K8s+Helm · Metrics · Prometheus · OTel · GitOps · Anchors · FAISS · PatchTST
- **Context banner**: cluster, namespace, entity count, K8s version, elapsed time
- **Retrieval expander**: BM25+FAISS→RRF stats — dense hits / sparse hits / fused hits / top RRF score
- **Helm drift table**: declared vs observed, colour-coded by severity
- **Reasoning journey**: collapsible path history (exhausted paths) + current best path
- **Root cause + remediation**: causal chain + executable `kubectl`/`helm` commands
- **Human review gate**: Approve / Reject with `Command(resume=…)` LangGraph handoff

## Tab 2 — Knowledge Base

| Sub-tab | Content |
|---|---|
| **Ontology** | Filterable entity browser — kind / namespace / name / annotation count |
| **Anchors** | Helm fix suggestions (manifest anchors on unhealthy pods) + full anchor records |
| **K8s Docs** | Version-adaptive links + Fetch & Index 16 key pages at detected cluster version |
| **Enterprise Docs** | Manual text / file upload / URL fetch (Confluence auto-detected) + tag filter |
| **Helm / Helmfile** | Upload `values.yaml`, `helmfile.yaml`, or `.tgz` chart archives — indexed as helm source documents |

## Tab 3 — Dashboard

- Ingestion pipeline step table (last run status + stats)
- Knowledge base metrics: enterprise docs / K8s docs / references / total content
- Tag breakdown bar chart
- Source weight configuration

## Tab 4 — Integration Tests

Cluster-free pipeline exploration — no Ollama required for **🔬 Pipeline trace** mode.

**Dropdown**: all registered test cases from:
- `cases/NNN_*/` — synthetic JSON fixtures (shown when `tests/unit/test_hybrid_pipeline_NNN.py` exists)
- `cases/helm_cases/h*/` — Helm chart cases (native YAML + values + observed)
- `tests/integration/cases/h*/` — native K8s cases (kube YAML + helm + helmfile + policy reports)

**Modes**:

| Mode | Requires Ollama | Description |
|---|---|---|
| 🔬 Pipeline trace | No | 10-step pre-LLM pipeline visualization (default) |
| Auto (full BFS) | Yes | Full dialogue simulation with LLM |
| Manual (step-by-step) | Yes | Interactive turn-by-turn simulation |

**Pipeline trace steps** (auto-runs on case selection, cached per case):

| Step | What it shows |
|---|---|
| 1 | BM25 tokenizer — query tokens |
| 2 | FAISS dense hits (cosine similarity) |
| 3 | BM25 sparse hits (keyword) |
| 4 | RRF fusion — dense + sparse metrics |
| 5 | Seeds — unhealthy resources + Warning events |
| 6 | **Anchor pivot table** — declared → observed → status → fix command |
| 7 | Jaccard deduplication — candidates / kept / diversity ratio |
| 8 | TF-IDF ranked context chunks |
| 9 | Pre-LLM confidence score breakdown |
| 10 | **Proposed changes** — values.yaml diff · `helm upgrade` commands · OPA/Kyverno fixes |
| Bonus | RemediationEngine rule-based hypotheses |
| Bonus | LLM prompt dry-run (full prompt preview) |

## Anchor pivot — declared → observed → fix

Anchors bridge chart intent to live cluster state to concrete remediation commands. The pivot covers **all deployment blockers**, not just value drift:

```
values.yaml declares:   resources.limits.memory = 512Mi
Observed (deployed):    resources.limits.memory = 128Mi        ← DRIFT
Fix:                    helm upgrade api -n production --set resources.limits.memory=512Mi

Pod references:         secret/payment-db-secret (envFrom)
Observed:               secret not found in namespace
Fix:                    kubectl create secret generic payment-db-secret -n production --from-literal=…

NetworkPolicy applied:  order-service-restrict-egress  egress: []
Effect:                 ALL outbound blocked (DNS 53, PostgreSQL 5432, Redis 6379)
Fix:                    kubectl edit networkpolicy order-service-restrict-egress -n production

ServiceAccount:         audit-exporter-sa  (exists, no RoleBinding)
Fix:                    kubectl create clusterrolebinding audit-exporter-rb --clusterrole=view --serviceaccount=production:audit-exporter-sa
```

The **Step 6** anchor table in the pipeline trace renders declared vs observed for every field:

| resource | field | declared | observed | source | status | fix |
|---|---|---|---|---|---|---|
| api | `resources.limits.memory` | `512Mi` | `128Mi` | values.yaml | 🔴 DRIFT | `helm upgrade api --set resources.limits.memory=512Mi` |
| api | `image.tag` | `v3.2.0` | `v3.2.0` | values.yaml | ✅ OK | — |

**Step 10** proposes all changes in priority order:
1. 🔴 **Missing deployment dependencies** — `kubectl create secret/configmap/serviceaccount/pvc`
2. 🌐 **NetworkPolicy blockers** — `kubectl edit networkpolicy` + egress rule template
3. 🔒 **OPA / Kyverno policy fixes** — policy-specific remediation hints
4. 🔀 **Helm drift** — `helm upgrade --set` to restore declared values
5. 📄 **Declared values.yaml** (collapsed reference)
