# KubeWhisperer — Live cluster demo
# Usage: vhs demo/cluster_demo.tape

Output demo/output/cluster_demo.gif

Set Shell      "bash"
Set FontSize   13
Set FontFamily "JetBrains Mono"
Set Width      1200
Set Height     800
Set Theme      "Dracula"
Set PlaybackSpeed 1
Set TypingSpeed 50ms
Set CursorBlink false
Set WaitTimeout 180s

# ── Shell init ────────────────────────────────────────────────────────────────
Env PATH "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
Env KUBECONFIG "/Users/ah/.kube/config"
Env KUBE_CONTEXT "k3d-k0rdent"
Env KUBE_NAMESPACES "kubewhisperer-demo"
Env LLM_PROVIDER "groq"
Env GROQ_MODEL "llama-3.3-70b-versatile"

Sleep 1s
Type "cd /Users/ah/workspaces/kubeWhisperer && clear"
Enter
Sleep 800ms

# ── Title ─────────────────────────────────────────────────────────────────────
Type "echo ''"
Enter
Type "echo '  ██╗  ██╗██╗   ██╗██████╗ ███████╗██╗    ██╗██╗  ██╗██╗███████╗██████╗ ███████╗██████╗'"
Enter
Type "echo '  ██║ ██╔╝██║   ██║██╔══██╗██╔════╝██║    ██║██║  ██║██║██╔════╝██╔══██╗██╔════╝██╔══██╗'"
Enter
Type "echo '  █████╔╝ ██║   ██║██████╔╝█████╗  ██║ █╗ ██║███████║██║███████╗██████╔╝█████╗  ██████╔╝'"
Enter
Type "echo ''"
Enter
Type "echo '  Kubernetes Root Cause Analysis  ·  Real k3d cluster  ·  Groq / Llama-3.3-70B'"
Enter
Sleep 2s
Type "clear"
Enter
Sleep 500ms

# ── ACT 1 — Healthy cluster ───────────────────────────────────────────────────
Type "echo '── ACT 1 · Healthy cluster ─────────────────────────────────────────'"
Enter
Sleep 500ms

Type "kubectl get pods -n kubewhisperer-demo"
Enter
Sleep 3s

Type "echo ''"
Enter
Type "echo '  ✓  All systems nominal — api-gateway 2/2 Running'"
Enter
Sleep 2s

# ── ACT 2 — Inject failures ───────────────────────────────────────────────────
Type "echo ''"
Enter
Type "echo '── ACT 2 · Injecting incident ──────────────────────────────────────'"
Enter
Sleep 500ms

Type "bash demo/cluster_setup.sh --inject"
Enter
Sleep 5s

Type "kubectl get pods -n kubewhisperer-demo"
Enter
Sleep 4s

Type "echo ''"
Enter
Type "echo '  ✗  payment-service CrashLoopBackOff   ✗  analytics-worker OOMKilled'"
Enter
Sleep 2s

# ── ACT 3 — Trigger RCA via webhook ──────────────────────────────────────────
Type "echo ''"
Enter
Type "echo '── ACT 3 · Alertmanager webhook → KubeWhisperer RCA ────────────────'"
Enter
Sleep 800ms

Type "python demo/demo_webhook.py --api http://localhost:8001"
Enter

# Wait for Groq analysis (~8s) + display
Sleep 15s

# ── End ───────────────────────────────────────────────────────────────────────
Sleep 3s
Type "echo ''"
Enter
Type "echo '  github.com/a1h8/kubeWhisperer'"
Enter
Sleep 3s
