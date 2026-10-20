# Security Policy

## Reporting a vulnerability

If you find a security issue in KubeVerdict, please **do not open a public GitHub issue**.

Report privately via one of these channels:
- **GitHub private advisory**: [Security tab → Report a vulnerability](https://github.com/a1h8/kube-verdict/security/advisories/new)
- **Email**: [redacted]

Include: a description of the issue, steps to reproduce, and the potential impact. I will acknowledge the report within 72 hours and aim to release a fix within 14 days for confirmed vulnerabilities.

## Scope

**In scope:**
- Injection vulnerabilities in `kubectl`/`helm` command construction
- Secrets or credentials leaking through logs or the Streamlit UI
- RBAC escalation if KubeVerdict requests more permissions than declared in `k8s/rbac.yaml`
- Dependencies with known CVEs (`pip audit`)

**Out of scope:**
- Vulnerabilities in the demo fixtures (`cases/`, `tests/integration/cases/`) — these are intentionally broken scenarios used for testing
- Issues requiring physical access to the cluster node
- Denial-of-service against a cluster KubeVerdict does not own

## Cluster permissions

KubeVerdict requires **read-only** cluster access (`get`, `list`, `watch`). The `ClusterRole` in `k8s/rbac.yaml` grants no write permissions; the server-side dry-run of a remediation needs the permission of the real action, so it is refused under that role. The Streamlit UI's manual panel can run a remediation line for the operator, with the operator's kubeconfig.

What leaves the machine: with the default `LLM_PROVIDER=ollama` (local) or `none`, no prompt does. A remote provider (`anthropic`, `openai`, `google`, `groq`) receives the prompt, which quotes cluster objects, events, logs and traces (never Secret values). The embedding model downloads once from HuggingFace unless its cache is pre-seeded. A GitHub repository configured for GitOps is cloned over the network.
