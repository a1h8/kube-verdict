#!/usr/bin/env bash
# KubeVerdict — complete demo initialisation.
# Runs from zero to a fully operational RCA demo on a fresh machine.
#
#   Linux (recommended)  → k3s installed natively
#   macOS / Docker       → k3d cluster in a container
#
# Usage:
#   bash demo/init.sh              # full setup
#   bash demo/init.sh --skip-k3s  # skip cluster creation (use existing)
#   bash demo/init.sh --skip-llm  # skip Ollama / Mistral install
#   bash demo/init.sh --uninstall  # tear everything down
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SKIP_K3S=false
SKIP_LLM=false
UNINSTALL=false
OLLAMA_MODEL="${OLLAMA_MODEL:-mistral}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${GREEN}  ✓${NC} $*"; }
warn()    { echo -e "${YELLOW}  ⚠${NC} $*"; }
error()   { echo -e "${RED}  ✗${NC} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}▶ $*${NC}"; }
substep() { echo -e "    ${CYAN}→${NC} $*"; }

# ── Argument parsing ──────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --skip-k3s)   SKIP_K3S=true ;;
    --skip-llm)   SKIP_LLM=true ;;
    --uninstall)  UNINSTALL=true ;;
  esac
done

# ── Uninstall ─────────────────────────────────────────────────────────────────
if $UNINSTALL; then
  step "Uninstalling KubeVerdict demo"
  bash "$SCRIPT_DIR/cleanup.sh"
  if command -v k3s &>/dev/null; then
    substep "Removing k3s..."
    sudo /usr/local/bin/k3s-uninstall.sh 2>/dev/null || true
  fi
  if command -v k3d &>/dev/null; then
    k3d cluster delete kubeverdict-demo 2>/dev/null || true
  fi
  info "Uninstall complete."
  exit 0
fi

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║       KubeVerdict — Full Demo Initialisation           ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  OS          : $(uname -s) $(uname -m)"
echo "  Project     : $PROJECT_ROOT"
echo "  LLM model   : $OLLAMA_MODEL"
echo ""

OS="$(uname -s)"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — System packages
# ─────────────────────────────────────────────────────────────────────────────
step "1/6  System packages"

_need() {
  command -v "$1" &>/dev/null && { info "$1 already installed"; return 0; }
  substep "Installing $1..."
  return 1
}

# curl
_need curl || {
  [[ "$OS" == "Linux" ]] && sudo apt-get install -y curl 2>/dev/null || \
    sudo yum install -y curl 2>/dev/null || true
}

# git
_need git || {
  [[ "$OS" == "Linux" ]] && sudo apt-get install -y git 2>/dev/null || \
    sudo yum install -y git 2>/dev/null || true
}

# kubectl
_need kubectl || {
  substep "Installing kubectl..."
  if [[ "$OS" == "Darwin" ]]; then
    brew install kubectl 2>/dev/null || {
      curl -sLO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/darwin/$(uname -m)/kubectl"
      chmod +x kubectl && sudo mv kubectl /usr/local/bin/
    }
  else
    KUBE_VER="$(curl -sL https://dl.k8s.io/release/stable.txt)"
    curl -sLO "https://dl.k8s.io/release/${KUBE_VER}/bin/linux/amd64/kubectl"
    chmod +x kubectl && sudo mv kubectl /usr/local/bin/
  fi
}

# helm
_need helm || {
  substep "Installing Helm..."
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
}

# Python3 / pip
_need python3 || error "Python 3.9+ is required. Install it first."
_need pip3    || error "pip3 not found."

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Python dependencies
# ─────────────────────────────────────────────────────────────────────────────
step "2/6  Python dependencies"

if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
  substep "pip install -r requirements.txt"
  pip3 install -q -r "$PROJECT_ROOT/requirements.txt"
  info "Python dependencies installed"
else
  warn "requirements.txt not found — skipping"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Kubernetes cluster
# ─────────────────────────────────────────────────────────────────────────────
step "3/6  Kubernetes cluster"

if $SKIP_K3S; then
  warn "--skip-k3s: using existing cluster"
  kubectl cluster-info &>/dev/null || error "No cluster available. Remove --skip-k3s or set KUBECONFIG."
else
  if kubectl cluster-info &>/dev/null 2>&1; then
    info "Existing cluster detected — skipping install"

  elif [[ "$OS" == "Darwin" ]]; then
    # macOS — use k3d (Docker required)
    command -v docker &>/dev/null || error "Docker Desktop required on macOS. Install it first."
    if ! _need k3d; then
      substep "Installing k3d..."
      brew install k3d 2>/dev/null || \
        curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
    fi
    substep "Creating k3d cluster..."
    k3d cluster create kubeverdict-demo \
      --agents 1 \
      --k3s-arg '--disable=traefik@server:*' \
      --wait
    export KUBECONFIG="$(k3d kubeconfig write kubeverdict-demo)"
    info "k3d cluster ready"

  else
    # Linux — install k3s natively
    substep "Installing k3s (this requires sudo)..."
    curl -sfL https://get.k3s.io | sudo INSTALL_K3S_EXEC="server \
      --disable traefik \
      --disable servicelb \
      --write-kubeconfig-mode 644" sh -

    export KUBECONFIG="/etc/rancher/k3s/k3s.yaml"

    substep "Waiting for k3s to be ready..."
    for i in $(seq 1 30); do
      kubectl cluster-info &>/dev/null 2>&1 && break || sleep 3
    done
    kubectl wait --for=condition=Ready node --all --timeout=120s
    info "k3s ready: $(kubectl get nodes --no-headers | awk '{print $1, $2}')"

    # Copy kubeconfig for current user
    REAL_USER="${SUDO_USER:-$USER}"
    REAL_HOME=$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6 || echo "$HOME")
    mkdir -p "$REAL_HOME/.kube"
    sudo cp /etc/rancher/k3s/k3s.yaml "$REAL_HOME/.kube/config" 2>/dev/null || true
    sudo chown "$REAL_USER:" "$REAL_HOME/.kube/config" 2>/dev/null || true
  fi
fi

# Verify metrics-server
substep "Checking metrics-server..."
for i in $(seq 1 15); do
  kubectl top nodes &>/dev/null 2>&1 && { info "metrics-server ready"; break; } || sleep 4
done
kubectl top nodes &>/dev/null 2>&1 || warn "metrics-server not ready — METRICS_SERVER_ENABLED will be set to false"
METRICS_OK=$(kubectl top nodes &>/dev/null 2>&1 && echo "true" || echo "false")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Ollama + LLM model
# ─────────────────────────────────────────────────────────────────────────────
step "4/6  Ollama + $OLLAMA_MODEL"

if $SKIP_LLM; then
  warn "--skip-llm: skipping Ollama install"
else
  bash "$SCRIPT_DIR/k3s/install_ollama.sh" --model "$OLLAMA_MODEL"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Deploy demo scenarios
# ─────────────────────────────────────────────────────────────────────────────
step "5/6  Deploying demo scenarios"
bash "$SCRIPT_DIR/setup.sh"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Smoke test
# ─────────────────────────────────────────────────────────────────────────────
step "6/6  Smoke test"

substep "Cluster nodes:"
kubectl get nodes -o wide

substep "Demo pods:"
kubectl get pods -n kubeverdict-demo -o wide

substep "Helm releases:"
helm list -n kubeverdict-demo

substep "Python imports..."
cd "$PROJECT_ROOT"
python3 -c "
import sys; sys.path.insert(0, '.')
import config, ontology.graph, ingestion.k8s_collector, rca.analyzer
from rca.remediation_engine import RemediationEngine
print('  All core imports OK')
"
info "Smoke test passed"

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║               Demo ready — run the RCA                  ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  python demo/run_rca.py"
echo ""
echo "  Scenarios deployed:"
echo "    payment-service      → CrashLoopBackOff  (DB unreachable)"
echo "    analytics-worker     → OOMKilled          (memory drift patch)"
echo "    notification-service → ConfigError        (missing ConfigMap + Secret)"
echo "    ml-inference         → ImagePullBackOff   (broken image drift)"
echo "    gpu-worker           → Pending            (no GPU node)"
echo "    api-gateway          → Running ✓"
echo ""
echo "  Rule-based fallback (LOW confidence) covers:"
echo "    OOMKill / CrashLoop-DB / ImagePull / MissingConfig"
echo "    Pending-Unschedulable / HelmDrift / DegradedDeployment"
echo ""
