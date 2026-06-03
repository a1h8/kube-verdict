# KubeVerdict

Evidence-first Kubernetes incident decision engine.

KubeVerdict correlates Kubernetes events and Helm drift into an evidence-grounded incident summary, then proposes human-approved remediation commands.

✅ Air-gapped by default — Ollama + Mistral, no data leaves your infrastructure  
✅ No auto-remediation without explicit approval  
✅ Ten failure scenarios reproduced end-to-end in CI as offline fixtures  
✅ Try it without a live cluster  

[![CI](https://github.com/a1h8/kube-verdict/actions/workflows/ci.yml/badge.svg)](https://github.com/a1h8/kube-verdict/actions/workflows/ci.yml)
[![Validated cases](https://img.shields.io/badge/validated%20cases-h001--h010-blue)](#validated-scenarios)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)

---

## Why it matters

Most Kubernetes outages are not caused by a single failing pod.

When payment-service crashes, the on-call engineer opens five tabs simultaneously: pod logs, Kubernetes events, Helm history, Prometheus graphs, and the GitOps repo. Under pressure, at 2 AM, with three Slack threads open. The root cause is rarely where the alert fired — it's three hops away in a misconfigured Helm value or a drift between what was declared and what actually runs.

KubeVerdict reduces that cognitive load. It starts from an Alertmanager incident signal, correlates Kubernetes events and Helm drift into a single evidence-grounded root cause analysis, ranks the diagnosis by confidence, and keeps a human approval gate before any remediation command touches production.

---

## Try without a cluster

The **Integration Tests** tab runs entirely offline — no cluster, no Ollama needed:

1. `streamlit run ui/app.py`
2. Go to **🧪 Integration Tests**
3. Select any `h00N_*` case from the dropdown
4. Mode defaults to **🔬 Pipeline trace** — pipeline runs automatically
5. Explore all 10 steps: tokenizer → retrieval → anchors → drift → confidence → proposed fixes

---

## Demo

### Browser UI — no cluster required

![KubeVerdict UI demo](demo/demo_kubeVerdict.gif)

```bash
bash demo/kap_record_ui.sh       # starts Streamlit, opens browser
```

---

### CLI — live k3d cluster, end-to-end loop

![KubeVerdict CLI demo](hero-demo-60s.gif)

Full loop on a real cluster: Alertmanager alert → RCA in 7s → human approval → cluster heals.

```
Alertmanager fires KubePodCrashLooping
        ↓  202 Accepted, session created
KubeVerdict ingests live K8s events
        ↓  7s
Root cause: "dial tcp db-primary:5432: connection refused — database initialisation failed"
Confidence: MEDIUM  ·  Blast radius: 5 resources
        ↓  human gate
Approve remediation? [y/N]  →  y
        ↓
db-primary deployed  ·  payment-service reconnected  ·  analytics-worker healed
All pods Running ✓
```

```bash
bash demo/kap_record.sh          # reset → baseline → start API
bash demo/cluster_setup.sh --inject
python demo/demo_webhook.py      # alert → RCA → approve → fix
```

→ [Full demo guide](docs/demo.md)

---

## Safety model

Every remediation command goes through two gates before execution:

1. **Human approval** — the SRE reviews evidence, root cause and proposed fix before anything is applied
2. **Rollback plan** — KubeVerdict generates the inverse command (`helm rollback`, `kubectl rollout undo`) alongside every fix proposal

A **blast-radius estimate** (LOW / MEDIUM / HIGH / CRITICAL) accompanies each proposal. It is currently a heuristic over the *proposed command* — it parses the kubectl/helm verb, namespace, resource kind, cluster-scope and affected-resource count. It is **not yet** a rendered-vs-live diff of the resources that would actually change; treat it as a triage signal, not a guarantee of impact.

Nothing touches the cluster without explicit sign-off. Autonomous execution is not implemented by design.

---

## How it works

The LLM is constrained by retrieved evidence. KubeVerdict ranks hypotheses from deterministic signals first — ontology topology, anchor violations, drift, policies and resolved incidents — then uses the LLM only to produce an evidence-grounded RCA.

Confidence routing is evidence-first: two consecutive LOW results on the same hypothesis path trigger an immediate switch to the next candidate, and archived paths re-rank remaining candidates using signals from the failed analysis.

**Pipeline:**

```
K8s events + Helm values/drift
        ↓
Ontology graph + anchor drift detection
        ↓
BM25 + FAISS + RRF hybrid retrieval
        ↓
Hypothesis ranking (evidence-weighted)
        ↓
LLM root-cause analysis (evidence-grounded)
        ↓
Human review gate → remediation commands
```

---

## Validated scenarios

Ten failure patterns reproduced end-to-end in CI as offline fixtures — no cluster, no Ollama required.

| Scenario | Case | What it proves |
|---|---|---|
| CrashLoopBackOff — missing dependency | h001 | BFS graph traversal, BM25+FAISS retrieval, anchor detection, confidence scoring, fix proposals |
| ImagePullBackOff — registry auth / tag drift | h002 | Helm drift detection, `drift.*` annotations, image proposal generation |
| OOMKilled — memory limit drift | h003 | Helm declared-vs-observed diff, `anchor_fix_hints()` → `helm upgrade --set` |
| Missing ConfigMap / Secret at pod start | h004 | `DeploymentReadinessDetector`, `missing.*` annotations, `kubectl create` hints |
| RBAC — missing ClusterRoleBinding | h005 | SA exists but no binding detected, `kubectl create clusterrolebinding` hint |
| NetworkPolicy egress block | h006 | `netpol.*` annotations, `kubectl edit networkpolicy` hints |
| HPA cannot scale — metrics-server unavailable | h007 | HPA target resolution, metrics-unavailable detection, `metrics-server` fix hints |
| Init container failing — DB migration exits 1 | h008 | `Init:0/1` detection, init-container log correlation, root-cause on migration failure |
| Liveness probe too aggressive — kills healthy pods | h009 | probe-timeout drift (`timeoutSeconds` declared vs deployed) → `helm upgrade` fix |
| ResourceQuota exceeded — pod stuck Pending | h010 | `ResourceQuota` entity, namespace quota correlation, pending-pod root cause |

Each case has a `test_hybrid_pipeline_NNN.py` running the full pre-LLM pipeline: graph construction → hybrid retrieval (BM25 + FAISS + RRF) → context building → anchor/drift/policy scoring → proposal generation. A further case — **h011 (StatefulSet update stuck on a bound PVC)** — is wired in but not yet passing its confidence/resolvable-path assertions; it is a good first contribution.

---

## Quick start

**Prerequisites:** Python 3.11+, a Kubernetes cluster reachable via kubeconfig, and one LLM provider configured in `.env`.

```bash
git clone https://github.com/a1h8/kube-verdict.git
cd kube-verdict
pip install -r requirements.txt

cp .env.example .env
# Edit .env: KUBECONFIG, LLM_PROVIDER, KUBE_NAMESPACES
# LLM_PROVIDER=ollama  → ollama pull mistral  (local, no data leaves infra)
# LLM_PROVIDER=groq    → set GROQ_API_KEY     (fast, free tier)
# LLM_PROVIDER=anthropic|openai|google → set corresponding API key

streamlit run ui/app.py
```

---

## Documentation

| Document | Content |
|---|---|
| [Architecture](docs/architecture.md) | Full pipeline diagram, LangGraph workflow, evidence-first hypothesis generation, anchor system design, drift detection |
| [REST API](docs/api.md) | FastAPI endpoints, session lifecycle, request/response examples, SSE stream |
| [UI reference](docs/ui.md) | Streamlit tabs, pipeline trace steps, anchor pivot table, reasoning journey, router decisions |
| [Test cases](docs/test-cases.md) | h001–h010 validated scenarios, case format, adding a new case, CI coverage |
| [Project layout](docs/project-layout.md) | Full directory tree, RBAC |
| [Roadmap](docs/roadmap.md) | Done and next |
| [Configuration](docs/configuration.md) | All `.env` variables, hybrid retrieval tuning, source weights |
| [Deployment](docs/deployment.md) | Docker, k3d, production K8s |

---

## Agent Skills / MCP integration

KubeVerdict exposes `kube_rca`, `helm_drift`, and `blast_radius` as MCP tools consumable by any MCP-compatible client (Claude Desktop, Cursor, Continue).

### Claude Desktop

```json
{
  "mcpServers": {
    "kube-verdict": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "/path/to/kube-verdict"
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "kube-verdict": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "/path/to/kube-verdict"
    }
  }
}
```

### Air-gapped clusters

All three tools run fully offline — Ollama + Mistral, no data leaves your infrastructure:

```bash
ollama serve &          # local LLM on port 11434
python mcp_server.py    # MCP stdio server, reads kubeconfig from env
```

**Embedding model:** the container image does not bake in the `all-MiniLM-L6-v2`
sentence-transformer (~90 MB) — it is fetched on first use. For a sealed
air-gapped deployment, pre-seed it once on a connected machine and mount the
cache into the container at `/app/.cache/huggingface`:

```bash
# on a connected machine — populates ~/.cache/huggingface
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
# then mount that dir at /app/.cache/huggingface (k8s: a PVC or hostPath volume)
```

See [SKILL.md](SKILL.md) for the full tool reference and air-gapped RBAC setup.

### OpenAI-compatible schema

The tool schema is also available in OpenAI function-calling format at [`openapi_tools.json`](openapi_tools.json),
compatible with any SDK that supports the function-calling API (OpenAI, LangChain, LlamaIndex).
It is generated from the MCP tool definitions — regenerate after changing a tool with:

```bash
python tools/gen_openapi_tools.py        # rewrite openapi_tools.json
python tools/gen_openapi_tools.py --check # CI guard: fail if stale
```

Feed `openapi_tools.json` as the `tools` array to any function-calling LLM, then
execute the model's tool calls against the same handlers the MCP server uses:

```python
from mcp_server import dispatch_openai_tool_call

for call in response.choices[0].message.tool_calls:
    tool_message = await dispatch_openai_tool_call(call.model_dump())
    messages.append(tool_message)   # role=tool, ready for the next turn
```

---

## Current limitations

Several constraints are intentional or known:

- **Validated cases: h001–h010** (h011 is wired in but not yet passing — see Validated scenarios). Advanced scenarios on the roadmap — Helmfile multi-release, MCTS routing, Slack/PagerDuty enrichment, RBAC-aware (impersonated) scoping — are not yet implemented.
- **Single-cluster.** Multi-cluster support is not yet wired end-to-end.
- **No auto-remediation in production.** The human approval gate is by design; autonomous execution is not implemented.
- **LLM performance is local-hardware-dependent.** Mistral via Ollama requires at least 8 GB RAM; a GPU significantly accelerates inference.
- **Primary validated inputs: Kubernetes events and Helm drift.** Prometheus, Loki, and OTel collectors exist and are wired in, but the E2E demo and validated test cases currently focus on the K8s events + Helm drift path.

See [Roadmap](docs/roadmap.md) for what's next.

---

## Contributing

Contributions are welcome — especially:

- New failure scenario cases (`tests/integration/cases/h0NN_*` format — see [Test cases](docs/test-cases.md))
- Signal-driven test cases that exercise the Prometheus, OTel, or Loki collector paths end-to-end
- LLM provider integrations

See [Project layout](docs/project-layout.md) for the codebase structure.

---

## Community

KubeVerdict is open-source and focused on Kubernetes incident investigation.
Feedback, reproducible failure scenarios and integrations are welcome — open an issue or start a discussion.

---

## License

[Apache 2.0](LICENSE)