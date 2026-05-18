# KubeWhisperer — Live cluster demo
# Usage: vhs demo/cluster_demo.tape

Output demo/cluster_demo.gif

Set Shell      "bash"
Set FontSize   13
Set FontFamily "JetBrains Mono"
Set Width      1100
Set Height     750
Set Theme      "Dracula"
Set PlaybackSpeed 1
Set TypingSpeed 40ms
Set CursorBlink false
Set WaitTimeout 120s

# ── Shell init ────────────────────────────────────────────────────────────────
Env PATH "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
Env KUBECONFIG "/Users/ah/.kube/config"
Env KUBE_CONTEXT "k3d-k0rdent"
Env KUBE_NAMESPACES "kubewhisperer-demo"
Env LLM_PROVIDER "groq"
Env GROQ_MODEL "llama-3.3-70b-versatile"

Sleep 2s
Type "cd /Users/ah/workspaces/kubeWhisperer"
Enter
Sleep 500ms
Type "clear"
Enter
Sleep 500ms

# ── Header ────────────────────────────────────────────────────────────────────
Type "echo '  KubeWhisperer — Live Cluster RCA Demo'"
Enter
Type "echo '  Real k3d cluster  |  Groq/Llama-70B  |  Human-in-the-loop'"
Enter
Type "echo ''"
Enter
Sleep 1s

# ── Show broken cluster state ─────────────────────────────────────────────────
Type "kubectl get pods -n kubewhisperer-demo"
Enter
Sleep 5s

# ── Show warning events ───────────────────────────────────────────────────────
Type "kubectl get events -n kubewhisperer-demo --sort-by=.lastTimestamp --field-selector type=Warning | tail -8"
Enter
Sleep 5s

# ── Launch RCA (--yes skips cluster prompt, human gate stays active) ──────────
Type "echo ''"
Enter
Type "echo '  Launching KubeWhisperer RCA...'"
Enter
Sleep 1s
Type "python3.11 demo/run_rca.py --yes"
Enter

# ── Wait for Groq analysis (~20s) ────────────────────────────────────────────
Sleep 30s

# ── Human approval gate — SRE reviews and approves ───────────────────────────
# The script pauses here and waits for input
Type "approve"
Enter
Sleep 3s
