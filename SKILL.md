---
name: kube-verdict
description: Anchor-by-render root-cause analysis for Kubernetes incidents — reconstructs the expected state from Helm/GitOps rendered manifests (when a chart or repo is available), compares it with the live cluster, and turns declared-vs-observed drift into a ranked diagnosis with remediation commands, a rollback plan and a confidence score. Use when pods are crashlooping, OOMKilled, ImagePullBackOff, Pending or stuck, when a Helm release has drifted from its declared values, or to assess the blast radius of a kubectl/helm fix before applying it. Exposes kube_rca, helm_drift and blast_radius as tools; runs air-gapped via local Ollama.
license: Apache-2.0
compatibility: Designed for Claude Code and MCP clients (Cursor, Continue). Runs air-gapped via Ollama + Mistral; requires Python 3.11+ and a read-only kubeconfig.
metadata:
  author: a1h8
  repository: https://github.com/a1h8/kube-verdict
---

# KubeVerdict — Claude Code Skill

KubeVerdict is a GitOps-aware Kubernetes incident decision engine built on **anchor-by-render**:
it reconstructs the expected state from Helm/GitOps rendered manifests and compares it with the
live cluster, so drift becomes RCA evidence rather than the LLM guessing from live symptoms alone.
Use it to run root-cause analysis, detect Helm drift, and assess remediation risk
directly from a Claude Code session — no browser, no copy-pasting kubectl output.

> The rendered-manifest path activates when a chart or GitOps repo is reachable
> (`GITOPS_REPO_URL`); without one, KubeVerdict falls back to the Helm-values-drift +
> K8s-schema anchor path. See [docs/anchor-by-render.md](docs/anchor-by-render.md).

## What this skill does

| Tool | What it does |
|------|-------------|
| `kube_rca` | Full RCA on a namespace — events, Helm drift, anchors → ranked diagnosis + remediation |
| `helm_drift` | Drift between declared Helm values and live cluster state (mode-aware: also diffs a pushed expected-state source when present) |
| `expected_state_drift` | Diff a pushed expected-state source — **Helm / Helmfile / Kustomize / raw manifests** — rendered at a pinned version vs live |
| `blast_radius` | Heuristic risk score (LOW→CRITICAL) over the proposed command + rollback check before applying any fix |

All tools run **air-gapped** by default: Ollama + Mistral, no data leaves your infrastructure.

---

## Quick start

### 1. Install KubeVerdict

```bash
git clone https://github.com/a1h8/kube-verdict
cd kube-verdict
pip install -e .
ollama pull mistral        # local LLM — no API key needed
```

### 2. Add to Claude Code (MCP)

Create or edit `~/.claude/mcp.json`:

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

Restart Claude Code. The three tools are now available in every session.

---

## Example prompts

```
Investigate crashlooping pods in the payment namespace
```
→ Claude calls `kube_rca(query="crashlooping pods", namespace="payment")`

```
Check if the api Helm release has drifted from its declared values in staging
```
→ Claude calls `helm_drift(release="api", namespace="staging")`

```
Before I apply these fixes, assess the blast radius:
  kubectl set image deployment/api api=registry/api:v2.1.0 -n production
  helm upgrade api ./chart -n production --set replicas=3
```
→ Claude calls `blast_radius(remediation_commands=[...])`

---

## Air-gapped environments

KubeVerdict is designed for air-gapped Kubernetes clusters:

- **LLM**: Ollama runs locally (`ollama serve`) — no outbound calls
- **Images**: Pull `ollama/ollama` and `mistral` once, then mirror to your internal registry
- **Dependencies**: `pip install` from a local PyPI mirror or bundle with `pip download`
- **kubeconfig**: Use an in-cluster ServiceAccount or a scoped kubeconfig with read-only RBAC

Minimal RBAC (read-only):
```yaml
rules:
  - apiGroups: [""]
    resources: ["pods", "events", "namespaces", "configmaps", "secrets", "persistentvolumeclaims", "resourcequotas"]
    verbs: ["get", "list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets", "statefulsets", "daemonsets"]
    verbs: ["get", "list"]
  - apiGroups: ["helm.sh"]
    resources: ["*"]
    verbs: ["get", "list"]
```

---

## Tool reference

### `kube_rca`

```json
{
  "query": "pods crashlooping",
  "namespace": "production",
  "kubeconfig": "/etc/kube/config",
  "kube_context": "prod-cluster"
}
```

Returns: `summary`, `root_cause`, `causal_chain`, `affected`, `remediation`, `rollback`, `confidence`, `pre_llm_confidence`

### `helm_drift`

```json
{
  "release": "api",
  "namespace": "staging",
  "kube_context": "staging-cluster"
}
```

Returns: `release`, `namespace`, `drift_count`, `drift_items[]` (field, declared, observed), `expected_state_mode`

### `expected_state_drift`

Deployment-mode agnostic — the pushed source may be a Helm chart, a Helmfile bundle,
a Kustomize overlay, or raw/rendered manifests (Jsonnet/Tanka, CDK8s, ArgoCD/Flux output).
The version is evidence: a different version renders a different expected baseline.

```json
{
  "chart": "payment-service",
  "version": "1.4.2",
  "namespace": "production",
  "kube_context": "prod-cluster"
}
```

Returns: `chart` (`name@version`), `mode`, `namespace`, `drift_count`, `drift_items[]` (field, declared, observed, severity)

### `blast_radius`

```json
{
  "remediation_commands": [
    "kubectl set image deployment/api api=registry/api:v2.1.0 -n production",
    "helm upgrade api ./chart -n production"
  ],
  "affected_resources": ["Pod/production/api-xyz"],
  "rollback_commands": ["helm rollback api -n production"]
}
```

Returns: `risk` (LOW/MEDIUM/HIGH/CRITICAL), `summary`, `namespaces`, `cluster_scoped`, `rollback_available`

---

## Policy gate

KubeVerdict never auto-applies fixes. Every remediation goes through:

1. **Blast radius** — risk scored before any action. The `blast_radius` tool uses a fast
   heuristic over the proposed command (verb / namespace / kind / cluster-scope /
   affected-count). A higher-fidelity **rendered-vs-live diff** method
   (`ManifestRenderer` + `ManifestDiffer` → the actual changed objects, classified by
   severity) is available when a chart and live cluster graph are present.
2. **Monte Carlo stability** — 200 simulations (±10% perturbation) on diagnosis confidence
3. **Policy gate** — AUTO (non-prod, LOW risk, MC win_rate ≥ 0.80) / HUMAN_REVIEW / NO_GO

Production namespaces always require explicit human approval.
