# Running the demo

## Prerequisites

**1. Local Kubernetes cluster (k3d)**

```bash
bash demo/cluster_setup.sh
```

Creates a k3d cluster named `k3d-k0rdent` and deploys broken workloads into the
`kubeverdict-demo` namespace (CrashLoopBackOff, OOMKilled, ImagePullBackOff).

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

## Decision UI demo

The React dashboard now covers the human decision gate as well:

```bash
python -m uvicorn api.app:app --reload
npm --prefix dashboard run dev
```

Then open `http://localhost:5173/#/journey`.

- `Load sample` shows a recorded decision journey snapshot
- `Investigate` runs a live session through the API
- when the session reaches `AWAITING_REVIEW`, the UI exposes `Approve remediation` / `Reject remediation`

## Prepared offline demos from `H0**`

For repeatable product demos, use the curated `H0**` integration cases in the Streamlit UI:

```bash
streamlit run ui/app.py
```

Then open `Integration Tests — Dialogue Simulation`.

Prepared demo starters now load directly from:

- `h001` CrashLoopBackOff — missing dependency
- `h002` ImagePullBackOff — registry auth / tag drift
- `h003` OOMKilled — memory limit drift
- `h004` missing ConfigMap / Secret
- `h005` RBAC forbidden
- `h006` NetworkPolicy blocked
- `h007` HPA metrics unavailable
- `h008` init container failure
- `h009` liveness probe timeout drift
- `h010` ResourceQuota exceeded

Recommended default: `h009_liveness_probe_loop` for an interactive reasoning demo, and `h006_networkpolicy_blocked` for a platform / networking demo.

## Demo storyline to show decision-making

Recommended sequence for a full product demo:

1. Start with `h009_liveness_probe_loop` in `Manual (step-by-step)`.
2. Use the `Strict demo` threshold profile to make a branch stall or regress.
3. Show the `dead end` state, then use `Backtrack` to explain that the system does not hide failed reasoning paths.
4. Run `↔ Compare strict vs lenient` on the same case to show how thresholding changes the exploration outcome.
5. Switch to `Lenient demo` and rerun the same case to show a cleaner convergence path.
6. Finish with `h006_networkpolicy_blocked` and use the operator decision panel to demonstrate that final remediation remains a human approval step.

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
