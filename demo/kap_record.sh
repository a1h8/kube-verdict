#!/usr/bin/env bash
# KubeVerdict — Kap demo recording setup
#
# Prepares the full environment then prints step-by-step Kap instructions.
#
# Usage:
#   bash demo/kap_record.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}  ✓${NC}  $*"; }
step()  { echo -e "\n${CYAN}──${NC}  $*"; }
warn()  { echo -e "${YELLOW}  ⚠${NC}  $*"; }
title() { echo -e "\n${BOLD}$*${NC}"; }

# ── Load .env ─────────────────────────────────────────────────────────────────
[[ -f .env ]] && { set -a; source .env; set +a; }

PROVIDER="${LLM_PROVIDER:-ollama}"

# ── LLM check ─────────────────────────────────────────────────────────────────
case "$PROVIDER" in
  groq)
    [[ -z "${GROQ_API_KEY:-}" ]] && { warn "GROQ_API_KEY not set in .env"; exit 1; }
    info "LLM: Groq / ${GROQ_MODEL:-llama-3.3-70b-versatile}  (~4-8s analysis)"
    ;;
  anthropic)
    [[ -z "${ANTHROPIC_API_KEY:-}" ]] && { warn "ANTHROPIC_API_KEY not set"; exit 1; }
    info "LLM: Anthropic / ${ANTHROPIC_MODEL:-claude-sonnet-4-6}  (~5-8s analysis)"
    ;;
  openai)
    [[ -z "${OPENAI_API_KEY:-}" ]] && { warn "OPENAI_API_KEY not set"; exit 1; }
    info "LLM: OpenAI / ${OPENAI_MODEL:-gpt-4o-mini}  (~5-10s analysis)"
    ;;
  ollama)
    OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
    if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
      warn "Ollama not reachable — start it with: ollama serve"; exit 1
    fi
    info "LLM: Ollama / ${OLLAMA_MODEL:-mistral}  (~60-120s analysis)"
    ;;
esac

# ── Reset + baseline ──────────────────────────────────────────────────────────
step "Resetting demo namespace..."
bash demo/cluster_setup.sh --reset
sleep 1

step "Deploying healthy baseline..."
bash demo/cluster_setup.sh --baseline

# ── Port-forwards ─────────────────────────────────────────────────────────────
step "Starting port-forwards (Alertmanager :9093)..."
bash demo/portforward.sh > /tmp/kw-portforward.log 2>&1 &
sleep 3

if curl -sf http://localhost:9093/-/healthy >/dev/null 2>&1; then
  info "Alertmanager ready on :9093"
else
  warn "Alertmanager port-forward may not be up yet — continuing"
fi

# ── Start KubeVerdict ───────────────────────────────────────────────────────
step "Starting KubeVerdict API on :8001..."
kill "$(cat /tmp/kubeverdict.pid 2>/dev/null)" 2>/dev/null || true
sleep 1
uvicorn api.app:app --host 0.0.0.0 --port 8001 --log-level info \
  > /tmp/kubeverdict.log 2>&1 &
echo $! > /tmp/kubeverdict.pid

printf "  Waiting for startup"
for i in $(seq 1 30); do
  if grep -q "Application startup complete" /tmp/kubeverdict.log 2>/dev/null; then
    echo " — ready"
    break
  fi
  printf "."
  sleep 0.5
done
info "KubeVerdict ready on :8001"

# ── Ready ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
title "  Environment ready — follow these steps to record with Kap"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}1.${NC} Open Kap, crop over your terminal window, start recording"
echo ""
echo -e "  ${BOLD}2.${NC} Show healthy cluster:"
echo -e "       ${CYAN}kubectl get pods -n kubeverdict-demo${NC}"
echo ""
echo -e "  ${BOLD}3.${NC} Inject the incident:"
echo -e "       ${CYAN}bash demo/cluster_setup.sh --inject${NC}"
echo ""
echo -e "  ${BOLD}4.${NC} Watch pods enter failure state:"
echo -e "       ${CYAN}kubectl get pods -n kubeverdict-demo${NC}"
echo ""
echo -e "  ${BOLD}5.${NC} Trigger RCA:"
echo -e "       ${CYAN}python demo/demo_webhook.py${NC}"
echo ""
echo -e "  ${BOLD}6.${NC} Stop Kap when the incident report appears (~5s)"
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
info "Logs: tail -f /tmp/kubeverdict.log"
info "Stop: kill \$(cat /tmp/kubeverdict.pid)"
echo ""
