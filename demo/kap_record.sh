#!/usr/bin/env bash
# KubeWhisperer — Kap browser demo launcher
#
# Usage:
#   bash demo/kap_record.sh
#
# Then in the browser:
#   1. Toggle "Auto mode" ON  (top-right)
#   2. Click "▶ Run analysis"
#   3. Wait for 🟢 Complete + green cluster table
#   4. Stop Kap recording
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[demo]${NC} $*"; }
step()  { echo -e "${CYAN}[demo]${NC} $*"; }
warn()  { echo -e "${YELLOW}[demo]${NC} $*"; }

# ── .env ──────────────────────────────────────────────────────────────────────
[[ -f .env ]] && { set -a; source .env; set +a; }

PROVIDER="${LLM_PROVIDER:-ollama}"
info "LLM provider: $PROVIDER"

# ── LLM reachability ──────────────────────────────────────────────────────────
if [[ "$PROVIDER" == "ollama" ]]; then
    OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
    if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
        warn "Ollama not reachable at $OLLAMA_URL"
        warn "Start it with: ollama serve"
        exit 1
    fi
    MODEL="${OLLAMA_MODEL:-qwen2.5:0.5b}"
    info "Ollama OK — model: $MODEL (expect ~15-30s analysis)"
fi

if [[ "$PROVIDER" == "anthropic" ]]; then
    [[ -z "${ANTHROPIC_API_KEY:-}" ]] && { warn "ANTHROPIC_API_KEY not set"; exit 1; }
    info "Anthropic — model: ${ANTHROPIC_MODEL:-claude-sonnet-4-6} (~5-8s analysis)"
fi

if [[ "$PROVIDER" == "openai" ]]; then
    [[ -z "${OPENAI_API_KEY:-}" ]] && { warn "OPENAI_API_KEY not set"; exit 1; }
    info "OpenAI — model: ${OPENAI_MODEL:-gpt-4o-mini} (~5-10s analysis)"
fi

if [[ "$PROVIDER" == "google" ]]; then
    [[ -z "${GOOGLE_API_KEY:-}" ]] && { warn "GOOGLE_API_KEY not set"; exit 1; }
    info "Google — model: ${GOOGLE_MODEL:-gemini-2.0-flash} (~2-3s analysis)"
fi

# ── Kill existing Streamlit ────────────────────────────────────────────────────
if pgrep -f "streamlit run" >/dev/null 2>&1; then
    info "Stopping existing Streamlit…"
    pkill -f "streamlit run" || true
    sleep 1
fi

# ── Start Streamlit ───────────────────────────────────────────────────────────
info "Starting Streamlit…"
/opt/homebrew/bin/python3.11 -m streamlit run demo/ui_demo.py \
    --server.port 8501 \
    --server.headless true \
    --theme.base dark \
    --theme.primaryColor "#7c3aed" \
    >/tmp/streamlit-demo.log 2>&1 &
STREAMLIT_PID=$!

# ── Wait for server ready ─────────────────────────────────────────────────────
printf "${GREEN}[demo]${NC} Waiting for server"
for i in $(seq 1 20); do
    if curl -sf http://localhost:8501 >/dev/null 2>&1; then
        echo " ready"
        break
    fi
    printf "."
    sleep 0.5
done

# ── Open browser ──────────────────────────────────────────────────────────────
info "Opening http://localhost:8501"
open "http://localhost:8501"

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  Kap recording steps${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
step "1. Open Kap and position crop over the browser window"
step "2. Start Kap recording"
step "3. In the browser:"
step "     a. Toggle [Auto mode] ON  (top-right)"
step "     b. Click [▶ Run analysis]"
step "     c. Wait for 🟢 Complete + green cluster state"
step "4. Stop Kap recording"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
warn "Streamlit PID: $STREAMLIT_PID  (Ctrl+C or close terminal to stop)"

wait $STREAMLIT_PID
