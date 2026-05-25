#!/usr/bin/env bash
# KubeWhisperer — demo recording launcher
#
# Prepares the environment (reset, portforward, uvicorn) then records
# the full incident demo via VHS.
#
# Usage:
#   bash demo/record_demo.sh           # GIF output
#   bash demo/record_demo.sh --mp4     # MP4 output (requires ffmpeg)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}  ✓${NC}  $*"; }
step() { echo -e "\n${CYAN}──${NC}  $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }

TAPE="demo/cluster_demo.tape"
OUTPUT_GIF="demo/output/cluster_demo.gif"
OUTPUT_MP4="demo/output/cluster_demo.mp4"

# ── Format selection ──────────────────────────────────────────────────────────
if [[ "${1:-}" == "--mp4" ]]; then
  sed -i '' 's|^Output .*|Output demo/output/cluster_demo.mp4|' "$TAPE"
  info "Output format: MP4"
else
  sed -i '' 's|^Output .*|Output demo/output/cluster_demo.gif|' "$TAPE"
  info "Output format: GIF"
fi

# ── Load .env ─────────────────────────────────────────────────────────────────
[[ -f .env ]] && { set -a; source .env; set +a; }

# ── Check GROQ key ────────────────────────────────────────────────────────────
if [[ -z "${GROQ_API_KEY:-}" ]]; then
  warn "GROQ_API_KEY not set in .env — aborting"
  exit 1
fi
info "LLM: Groq / ${GROQ_MODEL:-llama-3.3-70b-versatile}"

# ── Reset demo namespace ──────────────────────────────────────────────────────
step "Resetting demo namespace..."
bash demo/cluster_setup.sh --reset
sleep 1

# ── Deploy healthy baseline ───────────────────────────────────────────────────
step "Deploying healthy baseline..."
bash demo/cluster_setup.sh --baseline

# ── Port-forwards ─────────────────────────────────────────────────────────────
step "Starting port-forwards..."
bash demo/portforward.sh > /tmp/kw-portforward.log 2>&1 &
sleep 3

if ! curl -sf http://localhost:9093/-/healthy >/dev/null 2>&1; then
  warn "Alertmanager port-forward not ready — continuing anyway"
else
  info "Alertmanager ready on :9093"
fi

# ── Start KubeWhisperer ───────────────────────────────────────────────────────
step "Starting KubeWhisperer on :8001..."
kill "$(cat /tmp/kubewhisperer.pid 2>/dev/null)" 2>/dev/null || true
sleep 1
uvicorn api.app:app --host 0.0.0.0 --port 8001 --log-level info \
  > /tmp/kubewhisperer.log 2>&1 &
echo $! > /tmp/kubewhisperer.pid

# Wait for startup
for i in $(seq 1 20); do
  if grep -q "Application startup complete" /tmp/kubewhisperer.log 2>/dev/null; then
    info "KubeWhisperer ready on :8001"
    break
  fi
  sleep 0.5
done

# ── Record ────────────────────────────────────────────────────────────────────
step "Starting VHS recording..."
echo ""
vhs "$TAPE"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
if [[ "${1:-}" == "--mp4" ]]; then
  info "Saved: $OUTPUT_MP4"
  open "$OUTPUT_MP4" 2>/dev/null || true
else
  info "Saved: $OUTPUT_GIF"
  open "$OUTPUT_GIF" 2>/dev/null || true
fi
