#!/usr/bin/env bash
# Install Ollama and pull the Mistral model for KubeVerdict.
# Run as your normal user (no sudo needed for the pull).
#
# Usage:
#   bash demo/k3s/install_ollama.sh
#   bash demo/k3s/install_ollama.sh --model llama3.2  # use a different model
set -euo pipefail

MODEL="${1:-mistral}"
[[ "$MODEL" == "--model" ]] && MODEL="${2:-mistral}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── Install Ollama if absent ──────────────────────────────────────────────────
if command -v ollama &>/dev/null; then
  info "Ollama already installed: $(ollama --version 2>/dev/null || echo 'unknown version')"
else
  info "Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
  info "Ollama installed."
fi

# ── Start Ollama server ───────────────────────────────────────────────────────
if pgrep -x ollama &>/dev/null; then
  info "Ollama server already running."
else
  info "Starting Ollama server in background..."
  ollama serve &>/tmp/ollama.log &
  sleep 3
fi

# ── Pull model ────────────────────────────────────────────────────────────────
info "Pulling model '${MODEL}' (this may take a few minutes)..."
ollama pull "$MODEL"

# ── Verify ───────────────────────────────────────────────────────────────────
info "Testing model response..."
RESPONSE=$(ollama run "$MODEL" "Reply with exactly: OK" 2>/dev/null | head -1 || echo "")
if [[ "$RESPONSE" == *"OK"* ]]; then
  info "Model '${MODEL}' is working."
else
  warn "Model test returned: '$RESPONSE' — model may still be usable."
fi

echo ""
info "Ollama + ${MODEL} ready."
info "Update OLLAMA_MODEL=${MODEL} in demo/.env.demo if needed."
