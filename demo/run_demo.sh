#!/usr/bin/env bash
# KubeVerdict — VHS demo launcher
# No Kubernetes cluster required.
#
# Usage:
#   bash demo/run_demo.sh                                         # Ollama (default, ~60-120s per analysis)
#   LLM_PROVIDER=anthropic bash demo/run_demo.sh                  # Anthropic claude-sonnet-4-6 (~5-8s)
#   LLM_PROVIDER=openai    bash demo/run_demo.sh                  # OpenAI gpt-4o-mini (~5-10s)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[demo]${NC} $*"; }
warn() { echo -e "${YELLOW}[demo]${NC} $*"; }

# ── .env ──────────────────────────────────────────────────────────────────────
if [[ -f .env ]]; then
    info "Loading .env"
    set -a; source .env; set +a
else
    warn ".env not found — using defaults (LLM_PROVIDER=${LLM_PROVIDER:-ollama})"
fi

# ── LLM check ─────────────────────────────────────────────────────────────────
PROVIDER="${LLM_PROVIDER:-ollama}"
info "LLM provider: $PROVIDER"

if [[ "$PROVIDER" == "ollama" ]]; then
    OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
    if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
        warn "Ollama not reachable at $OLLAMA_URL — start it with: ollama serve"
        warn "For a faster demo: LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... bash demo/run_demo.sh"
        exit 1
    fi
    info "Ollama reachable at $OLLAMA_URL (analysis ~60-120s)"
fi

if [[ "$PROVIDER" == "anthropic" ]]; then
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        warn "ANTHROPIC_API_KEY not set — set it in .env or export it before running"
        exit 1
    fi
    info "Anthropic configured (model: ${ANTHROPIC_MODEL:-claude-sonnet-4-6}, analysis ~5-8s)"
fi

if [[ "$PROVIDER" == "openai" ]]; then
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
        warn "OPENAI_API_KEY not set — set it in .env or export it before running"
        exit 1
    fi
    info "OpenAI configured (model: ${OPENAI_MODEL:-gpt-4o-mini}, analysis ~5-10s)"
fi

# ── Launch ────────────────────────────────────────────────────────────────────
info "Starting demo UI…"
info "Open: http://localhost:8501"
echo ""
streamlit run demo/ui_demo.py \
    --server.port 8501 \
    --server.headless true \
    --theme.base dark \
    --theme.primaryColor "#7c3aed"
