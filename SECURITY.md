# Security Policy

## Reporting a vulnerability

If you find a security issue in KubeWhisperer, please **do not open a public GitHub issue**.

Report privately via one of these channels:
- **GitHub private advisory**: [Security tab → Report a vulnerability](https://github.com/a1h8/KubeWhisperer/security/advisories/new)
- **Email**: a.heissat@gmail.com

Include: a description of the issue, steps to reproduce, and the potential impact. I will acknowledge the report within 72 hours and aim to release a fix within 14 days for confirmed vulnerabilities.

## Scope

**In scope:**
- Injection vulnerabilities in `kubectl`/`helm` command construction
- Secrets or credentials leaking through logs or the Streamlit UI
- RBAC escalation if KubeWhisperer requests more permissions than declared in `k8s/rbac.yaml`
- Dependencies with known CVEs (`pip audit`)

**Out of scope:**
- Vulnerabilities in the demo fixtures (`cases/`, `tests/integration/cases/`) — these are intentionally broken scenarios used for testing
- Issues requiring physical access to the cluster node
- Denial-of-service against a cluster KubeWhisperer does not own

## Cluster permissions

KubeWhisperer requires **read-only** cluster access (`get`, `list`, `watch`). The `ClusterRole` in `k8s/rbac.yaml` grants no write permissions. No data is sent to external services — all LLM inference runs locally via Ollama.
