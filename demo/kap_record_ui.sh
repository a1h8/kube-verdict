#!/usr/bin/env bash
# KubeVerdict — Kap browser demo launcher
#
# Launches the Streamlit demo UI and opens the browser.
# No Kubernetes cluster required.
#
# Usage:
#   bash demo/kap_record_ui.sh
#
# Kap recording steps:
#   1. Open Kap, crop over the browser window, start recording
#   2. Toggle "Auto mode" ON  (top-right)
#   3. Click "▶ Run analysis"
#   4. Wait for 🟢 Complete + green cluster table
#   5. Stop Kap recording
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[demo]${NC} $*"; }
step() { echo -e "${CYAN}[demo]${NC} $*"; }
warn() { echo -e "${YELLOW}[demo]${NC} $*"; }

# ── .env ──────────────────────────────────────────────────────────────────────
[[ -f .env ]] && { set -a; source .env; set +a; }

PROVIDER="${LLM_PROVIDER:-ollama}"
info "LLM provider: $PROVIDER"

# ── LLM reachability ──────────────────────────────────────────────────────────
case "$PROVIDER" in
  ollama)
    OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
    if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
      warn "Ollama not reachable at $OLLAMA_URL — start it with: ollama serve"
      exit 1
    fi
    info "Ollama OK — model: ${OLLAMA_MODEL:-mistral}  (~15-30s analysis)"
    ;;
  anthropic)
    [[ -z "${ANTHROPIC_API_KEY:-}" ]] && { warn "ANTHROPIC_API_KEY not set"; exit 1; }
    info "Anthropic — model: ${ANTHROPIC_MODEL:-claude-sonnet-4-6}  (~5-8s analysis)"
    ;;
  openai)
    [[ -z "${OPENAI_API_KEY:-}" ]] && { warn "OPENAI_API_KEY not set"; exit 1; }
    info "OpenAI — model: ${OPENAI_MODEL:-gpt-4o-mini}  (~5-10s analysis)"
    ;;
  groq)
    [[ -z "${GROQ_API_KEY:-}" ]] && { warn "GROQ_API_KEY not set"; exit 1; }
    info "Groq — model: ${GROQ_MODEL:-llama-3.3-70b-versatile}  (~4-8s analysis)"
    ;;
esac

# ── Kill existing Streamlit ────────────────────────────────────────────────────
if pgrep -f "streamlit run" >/dev/null 2>&1; then
  info "Stopping existing Streamlit…"
  pkill -f "streamlit run" || true
  sleep 1
fi

# ── Start Streamlit ───────────────────────────────────────────────────────────
info "Starting Streamlit…"
python3 -m streamlit run demo/ui_demo.py \
  --server.port 8501 \
  --server.headless true \
  --theme.base dark \
  --theme.primaryColor "#7c3aed" \
  > /tmp/streamlit-demo.log 2>&1 &
STREAMLIT_PID=$!

printf "${GREEN}[demo]${NC} Waiting for server"
for i in $(seq 1 20); do
  if curl -sf http://localhost:8501 >/dev/null 2>&1; then
    echo " ready"
    break
  fi
  printf "."; sleep 0.5
done

# ── Open browser ──────────────────────────────────────────────────────────────
info "Opening http://localhost:8501"
open "http://localhost:8501"

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  Kap recording steps (browser demo)${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
step "1. Open Kap, crop over the browser window, start recording"
step "2. Toggle [Auto mode] ON  (top-right)"
step "3. Click [▶ Run analysis]"
step "4. Wait for 🟢 Complete + green cluster table"
step "5. Stop Kap recording"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
warn "Streamlit PID: $STREAMLIT_PID  (Ctrl+C or close terminal to stop)"

wait $STREAMLIT_PID
