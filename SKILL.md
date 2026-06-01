# KubeVerdict — Claude Code Skill

KubeVerdict is an evidence-first Kubernetes incident decision engine.
Use it to run root-cause analysis, detect Helm drift, and assess remediation risk
directly from a Claude Code session — no browser, no copy-pasting kubectl output.

## What this skill does

| Tool | What it does |
|------|-------------|
| `kube_rca` | Full RCA on a namespace — events, Helm drift, anchors → ranked diagnosis + remediation |
| `helm_drift` | Drift between declared Helm values and live cluster state |
| `blast_radius` | Risk score (LOW→CRITICAL) + rollback check before applying any fix |

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

Returns: `release`, `namespace`, `drift_count`, `drift_items[]` (field, declared, observed)

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

1. **Blast radius** — risk scored before any action
2. **Monte Carlo stability** — 200 simulations on diagnosis confidence
3. **Policy gate** — AUTO (non-prod, LOW risk, MC stable) / HUMAN_REVIEW / NO_GO

Production namespaces always require explicit human approval.
