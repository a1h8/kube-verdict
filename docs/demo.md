# Running the demo

## Prerequisites

**1. Local Kubernetes cluster (k3d)**

```bash
bash demo/cluster_setup.sh
```

Creates a k3d cluster named `k3d-k0rdent` and deploys broken workloads into the
`kubewhisperer-demo` namespace (CrashLoopBackOff, OOMKilled, ImagePullBackOff).

**2. LLM — two modes**

| Mode | Config | Speed | Air-gapped |
|---|---|---|---|
| Local / air-gapped | `LLM_PROVIDER=ollama` + `OLLAMA_MODEL=mistral` | ~30–90 s depending on hardware | ✅ yes |
| Demo / connected | `LLM_PROVIDER=groq` + `GROQ_API_KEY=...` | ~5 s | ❌ no |

For local mode:
```bash
ollama serve &
ollama pull mistral
```

---

## Running the RCA

```bash
# Standard demo — skips cluster confirmation, shows human approval gate
python demo/run_rca.py --yes

# Fully automated (CI / scripted)
python demo/run_rca.py --yes --auto-approve

# Interactive — asks for cluster confirmation + human approval gate
python demo/run_rca.py

# Custom query
python demo/run_rca.py "Why is payment-service down?" --yes
```

Output is printed to stdout and saved to `demo/output/rca_<timestamp>.txt`.

---

## Recording the terminal demo (VHS)

```bash
# Full RCA + human gate
vhs demo/cluster_demo.tape

# Healthy service verification (no false positives)
vhs demo/healthy_check.tape
```

Requires [VHS](https://github.com/charmbracelet/vhs) (`brew install vhs`).

---

## What the output contains

| Section | Content |
|---|---|
| Cluster state collection | Node/pod/deployment counts, Helm releases |
| GitOps drift detection | Declared vs observed Helm values |
| Unhealthy resources & events | Top warning events per resource |
| Signal analysis (PatchTST) | Anomaly scores from metrics-server / Prometheus |
| INCIDENT SUMMARY | Severity, confidence, root cause (3 lines), proposed fix |
| FULL ANALYSIS — Root cause chain | Full LLM narrative |
| FULL ANALYSIS — Evidence by resource | Per-pod: phase, restarts, matched events, signal scores |
| FULL ANALYSIS — Remediation commands | Numbered kubectl commands to apply |
| Human approval gate | `approve` / `reject` — only `approve` triggers execution |
