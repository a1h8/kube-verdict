# KubeWhisperer — Healthy pod verification scenario
# Shows that KubeWhisperer correctly identifies a healthy service (no false positives)
# Usage: vhs demo/healthy_check.tape

Output demo/healthy_check.gif

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
Type "echo '  KubeWhisperer — Healthy Service Verification'"
Enter
Type "echo '  Scenario: is api-gateway healthy? No false positives.'"
Enter
Type "echo ''"
Enter
Sleep 1s

# ── Show pod state — api-gateway is Running ───────────────────────────────────
Type "kubectl get pods -n kubewhisperer-demo -l app=api-gateway"
Enter
Sleep 4s

# ── Show no warning events for api-gateway ────────────────────────────────────
Type "kubectl get events -n kubewhisperer-demo --field-selector involvedObject.name=api-gateway,type=Warning 2>/dev/null || echo '  No warning events.'"
Enter
Sleep 4s

# ── Run targeted RCA query ────────────────────────────────────────────────────
Type "echo ''"
Enter
Type "echo '  Querying KubeWhisperer...'"
Enter
Sleep 1s
Type "python3.11 demo/run_rca.py --yes \"Is the api-gateway service healthy and operating normally?\""
Enter

# ── Wait for Groq (~15s) ──────────────────────────────────────────────────────
Sleep 25s

# ── Human gate — no action needed, reject closes loop ────────────────────────
Type "reject"
Enter
Sleep 3s
