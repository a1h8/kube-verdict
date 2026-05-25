#!/usr/bin/env bash
# Install k3s for KubeVerdict demo.
# Run as root or with sudo.
#
# What this does:
#   1. Install k3s with metrics-server enabled, traefik disabled
#   2. Copy kubeconfig to ~/.kube/config (readable by current user)
#   3. Wait for the cluster to be ready
#   4. Print cluster info
#
# Supported: Linux x86_64 / arm64 (including Raspberry Pi 4/5)
#
# Usage:
#   sudo bash demo/k3s/install.sh
#   sudo bash demo/k3s/install.sh --uninstall
set -euo pipefail

KUBECONFIG_DEST="${HOME}/.kube/config"
K3S_KUBECONFIG="/etc/rancher/k3s/k3s.yaml"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Uninstall mode ────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
  info "Uninstalling k3s..."
  /usr/local/bin/k3s-uninstall.sh 2>/dev/null || true
  rm -f "$KUBECONFIG_DEST"
  info "k3s uninstalled."
  exit 0
fi

# ── Check root ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || error "Run with sudo: sudo bash demo/k3s/install.sh"

# ── Install k3s ───────────────────────────────────────────────────────────────
if command -v k3s &>/dev/null && k3s --version &>/dev/null; then
  info "k3s already installed: $(k3s --version | head -1)"
else
  info "Installing k3s (latest stable)..."
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server \
    --disable traefik \
    --disable servicelb \
    --write-kubeconfig-mode 644" sh -
  info "k3s installed."
fi

# ── Start k3s if not running ──────────────────────────────────────────────────
if ! systemctl is-active --quiet k3s 2>/dev/null; then
  info "Starting k3s service..."
  systemctl enable k3s
  systemctl start k3s
fi

# ── Wait for API server ───────────────────────────────────────────────────────
info "Waiting for API server to be ready..."
for i in $(seq 1 30); do
  if KUBECONFIG="$K3S_KUBECONFIG" kubectl cluster-info &>/dev/null 2>&1; then
    break
  fi
  sleep 2
done
KUBECONFIG="$K3S_KUBECONFIG" kubectl cluster-info || error "API server not ready after 60s"

# ── Wait for nodes ────────────────────────────────────────────────────────────
info "Waiting for node to be Ready..."
KUBECONFIG="$K3S_KUBECONFIG" kubectl wait --for=condition=Ready \
  node --all --timeout=120s

# ── Wait for metrics-server ───────────────────────────────────────────────────
info "Waiting for metrics-server to be available..."
for i in $(seq 1 20); do
  if KUBECONFIG="$K3S_KUBECONFIG" kubectl top nodes &>/dev/null 2>&1; then
    info "metrics-server is ready."
    break
  fi
  sleep 3
done

# ── Copy kubeconfig ───────────────────────────────────────────────────────────
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
KUBECONFIG_DEST="$REAL_HOME/.kube/config"

mkdir -p "$REAL_HOME/.kube"
cp "$K3S_KUBECONFIG" "$KUBECONFIG_DEST"
chown "$REAL_USER:$(id -gn "$REAL_USER")" "$KUBECONFIG_DEST"
chmod 600 "$KUBECONFIG_DEST"
info "kubeconfig written to $KUBECONFIG_DEST"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
info "k3s is ready."
KUBECONFIG="$K3S_KUBECONFIG" kubectl get nodes -o wide
echo ""
info "Next step: run demo setup"
echo "  bash demo/setup.sh"
