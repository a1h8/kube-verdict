# KubeWhisperer

> Automated Root Cause Analysis for Kubernetes — multi-path LLM reasoning with pluggable providers (Ollama · Groq · Anthropic · OpenAI · Google Gemini).

[![Tests](https://img.shields.io/badge/tests-1372%2B%20passed-brightgreen)](#validated-demo-scope)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)

KubeWhisperer combines a typed Kubernetes ontology, a GitOps drift engine, real-time observability ingestion (Prometheus, OTel/Tempo/Jaeger, Loki), an evidence-first multi-path reasoning workflow (LangGraph + beam search), a hybrid BM25+FAISS+RRF retrieval pipeline, anchor-driven manifest drift detection, a FastAPI REST API, and SQLite-backed persistence (sessions, checkpoints, FAISS reconstruction). LLM provider is pluggable: Ollama (local/air-gapped), Groq, Anthropic, OpenAI, or Google Gemini.

The LLM is a **next-token predictor over the top-k retrieved context** — it does not reason from scratch. Hypotheses are generated from deterministic evidence (ontology topology, anchor violations, RemediationEngine rules, past resolved incidents) before the LLM is invoked. Confidence routing uses a beam-search strategy: two consecutive LOW results on the same path trigger an immediate switch to the next candidate, and archived paths re-rank remaining candidates using signals from the failed analysis.

---

## Quick start

**Prerequisites:** Python 3.11+, a Kubernetes cluster reachable via kubeconfig, and one LLM provider configured in `.env`.

```bash
git clone https://github.com/a1h8/KubeWhisperer.git
cd KubeWhisperer
pip install -r requirements.txt

cp .env.example .env
# Edit .env: KUBECONFIG, LLM_PROVIDER, KUBE_NAMESPACES
# LLM_PROVIDER=ollama  → ollama pull mistral  (local, no data leaves infra)
# LLM_PROVIDER=groq    → set GROQ_API_KEY     (fast, free tier)
# LLM_PROVIDER=anthropic|openai|google → set corresponding API key

streamlit run ui/app.py
```

### Try without a cluster

The **Integration Tests** tab runs entirely offline — no cluster, no Ollama needed:

1. `streamlit run ui/app.py`
2. Go to **🧪 Integration Tests**
3. Select any `h00N_*` case from the dropdown
4. Mode defaults to **🔬 Pipeline trace** — pipeline runs automatically
5. Explore all 10 steps: tokenizer → retrieval → anchors → drift → confidence → proposed fixes

---

## Validated demo scope

Six scenarios are proven end-to-end in CI — no cluster, no Ollama required.

| Scenario | Case | What it proves |
|---|---|---|
| CrashLoopBackOff — missing dependency | h001 | BFS graph traversal, BM25+FAISS retrieval, anchor detection, confidence scoring, fix proposals |
| ImagePullBackOff — registry auth / tag drift | h002 | Helm drift detection, `drift.*` annotations, image proposal generation |
| OOMKilled — memory limit drift | h003 | Helm declared-vs-observed diff, `anchor_fix_hints()` → `helm upgrade --set` |
| Missing ConfigMap / Secret at pod start | h004 | `DeploymentReadinessDetector`, `missing.*` annotations, `kubectl create` hints |
| NetworkPolicy egress block | h005 | `netpol.*` annotations, `kubectl edit networkpolicy` hints |
| RBAC — missing ClusterRoleBinding | h006 | SA exists but no binding detected, `kubectl create clusterrolebinding` hint |

Each case runs the full pre-LLM pipeline: graph construction → hybrid retrieval (BM25 + FAISS + RRF) → context building → anchor/drift/policy scoring → proposal generation.

---

## Demo

![KubeWhisperer demo](demo/demo_kubeWhisperer.gif)

No real Kubernetes cluster required. The demo runs entirely offline against a pre-built incident scenario.

```bash
# LLM_PROVIDER=groq|anthropic|openai|ollama|demo
bash demo/kap_record.sh   # starts Streamlit + opens browser
```

The scenario injects four independent failures into a fake `kubewhisperer-demo` namespace:

| Service | Failure | Root cause |
|---|---|---|
| `payment-api` | CrashLoopBackOff | `db-primary` scaled to 0 (Helm drift) — connection refused |
| `analytics-worker` | OOMKilled | Memory limit drift: deployed 50Mi vs Helm chart 256Mi |
| `notification-svc` | ImagePullBackOff | Image tag drift: manifest `v3.2.1`, cluster resolved `:latest` (removed) |
| `ml-inference` | Pending | No schedulable node with `nvidia.com/gpu` |
| `api-gateway` | Running ✓ | Healthy baseline |

**What the analysis produces:**
- Confidence: HIGH (LLM-evaluated from anchor violations + causal chain evidence)
- Git diffs computed from Helm/manifest anchor annotations — not hardcoded
- Remediation commands validated by dry-run before display
- Human review gate — auto-approvable or manual

---

## Documentation

| Document | Content |
|---|---|
| [Architecture](docs/architecture.md) | Full pipeline diagram, LangGraph workflow, evidence-first hypothesis generation, beam search routing, anchor system design, drift detection, PatchTST |
| [UI reference](docs/ui.md) | Streamlit tabs, pipeline trace steps, anchor pivot table, reasoning journey, router decisions |
| [Test cases](docs/test-cases.md) | h001–h011 format, adding a new case, validated scope, CI coverage |
| [Project layout](docs/project-layout.md) | Full directory tree, RBAC |
| [Roadmap](docs/roadmap.md) | Done and next |
| [Configuration](docs/configuration.md) | All `.env` variables, hybrid retrieval tuning, source weights |
| [Deployment](docs/deployment.md) | Docker, k3d, production K8s |

---

## License

[Apache 2.0](LICENSE)
