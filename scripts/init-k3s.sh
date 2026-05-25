#!/usr/bin/env bash
# init-k3s.sh — Bootstrap KubeVerdict on a fresh K3s node.
#
# What this script does:
#   1. Installs K3s (single-node, no traefik)
#   2. Creates the kubeverdict namespace
#   3. Applies RBAC, Ollama, and KubeVerdict manifests
#   4. Waits for Ollama to be ready, then pulls the Mistral model
#   5. Triggers an on-demand KubeVerdict Job to verify the install
#
# Prerequisites:
#   - Ubuntu 22.04 / Debian 12 / RHEL 9 (x86_64 or arm64)
#   - curl, kubectl in PATH (kubectl is installed by K3s automatically)
#   - Run as root or with sudo
#   - Outbound internet access for the initial model pull
#     (subsequent runs are air-gapped once the PVC is populated)
#
# Usage:
#   sudo bash scripts/init-k3s.sh
#   sudo bash scripts/init-k3s.sh --image ghcr.io/your-org/kubeverdict:v1.0.0
#
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
K3S_VERSION="${K3S_VERSION:-v1.30.0+k3s1}"
NAMESPACE="kubeverdict"
MANIFESTS_DIR="$(cd "$(dirname "$0")/../k8s" && pwd)"
IMAGE="${1:-}"  # optional: --image <registry/image:tag>

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

# ── 1. Install K3s ───────────────────────────────────────────────────────────
if ! command -v k3s &>/dev/null; then
    log "Installing K3s ${K3S_VERSION}…"
    curl -sfL https://get.k3s.io | \
        INSTALL_K3S_VERSION="${K3S_VERSION}" \
        INSTALL_K3S_EXEC="server --disable traefik --disable servicelb" \
        sh -
    log "K3s installed."
else
    log "K3s already installed: $(k3s --version | head -1)"
fi

# Make kubectl available without sudo
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME=$(getent passwd "${SUDO_USER}" | cut -d: -f6)
    mkdir -p "${REAL_HOME}/.kube"
    cp /etc/rancher/k3s/k3s.yaml "${REAL_HOME}/.kube/config"
    chown "${SUDO_USER}:${SUDO_USER}" "${REAL_HOME}/.kube/config"
    chmod 600 "${REAL_HOME}/.kube/config"
fi

# Wait for API server
log "Waiting for K3s API server…"
until kubectl get nodes &>/dev/null 2>&1; do sleep 2; done
kubectl wait node --all --for=condition=Ready --timeout=120s
log "Node(s) ready."

# ── 2. Namespace ─────────────────────────────────────────────────────────────
log "Creating namespace ${NAMESPACE}…"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# ── 3. Apply manifests ───────────────────────────────────────────────────────
log "Applying RBAC…"
kubectl apply -f "${MANIFESTS_DIR}/rbac.yaml"

log "Applying Ollama (Deployment + PVC + Service)…"
kubectl apply -f "${MANIFESTS_DIR}/ollama.yaml"

# Patch image if provided
if [[ -n "${IMAGE}" && "${IMAGE}" != "--image" ]]; then
    ACTUAL_IMAGE="${IMAGE}"
elif [[ "$#" -ge 2 && "$1" == "--image" ]]; then
    ACTUAL_IMAGE="$2"
fi

if [[ -n "${ACTUAL_IMAGE:-}" ]]; then
    log "Patching KubeVerdict image to ${ACTUAL_IMAGE}…"
    sed "s|ghcr.io/your-org/kubeverdict:latest|${ACTUAL_IMAGE}|g" \
        "${MANIFESTS_DIR}/kubeverdict.yaml" | kubectl apply -f -
else
    kubectl apply -f "${MANIFESTS_DIR}/kubeverdict.yaml"
fi

# ── 4. Wait for Ollama, then pull Mistral ────────────────────────────────────
log "Waiting for Ollama Deployment to be ready…"
kubectl rollout status deployment/ollama -n "${NAMESPACE}" --timeout=120s

log "Triggering Mistral model pull (this takes ~2-5 min on first run)…"
# The ollama-pull-mistral Job is defined in ollama.yaml
# If it was already applied, wait for it:
if kubectl get job ollama-pull-mistral -n "${NAMESPACE}" &>/dev/null; then
    kubectl wait job/ollama-pull-mistral -n "${NAMESPACE}" \
        --for=condition=complete --timeout=600s \
        || log "WARNING: model pull job did not complete in 10 min — check logs with: kubectl logs -n ${NAMESPACE} -l app=ollama-pull"
fi

# ── 5. Verify with an on-demand run ─────────────────────────────────────────
log "Triggering on-demand KubeVerdict analysis…"
kubectl create job \
    --from=cronjob/kubeverdict \
    kubeverdict-verify \
    -n "${NAMESPACE}" \
    --dry-run=client -o yaml \
    | kubectl apply -f - || true

log "Waiting for verification job…"
kubectl wait job/kubeverdict-verify -n "${NAMESPACE}" \
    --for=condition=complete --timeout=300s \
    || log "Job still running — check with: kubectl logs -n ${NAMESPACE} -l app=kubeverdict"

log ""
log "──────────────────────────────────────────────────────────────────"
log "KubeVerdict is running on K3s."
log ""
log "Useful commands:"
log "  # Watch scheduled analyses:"
log "  kubectl get jobs -n ${NAMESPACE} -w"
log ""
log "  # Trigger an ad-hoc analysis:"
log "  kubectl create job --from=cronjob/kubeverdict kw-adhoc -n ${NAMESPACE}"
log "  kubectl logs -n ${NAMESPACE} -l app=kubeverdict -f"
log ""
log "  # Check Ollama is healthy:"
log "  kubectl exec -n ${NAMESPACE} deploy/ollama -- ollama list"
log "──────────────────────────────────────────────────────────────────"
