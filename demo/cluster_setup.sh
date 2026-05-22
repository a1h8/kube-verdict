#!/usr/bin/env bash
# KubeWhisperer — local cluster demo setup
# Creates a k3d cluster and injects real Kubernetes failures.
# Usage: bash demo/cluster_setup.sh [--reset]
set -euo pipefail

CLUSTER="kubewhisperer-demo"
NS="kubewhisperer-demo"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFESTS="$ROOT/demo/manifests"

# ── Reset ─────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--reset" ]]; then
    echo "  Deleting cluster $CLUSTER..."
    k3d cluster delete "$CLUSTER" 2>/dev/null || true
    exit 0
fi

# ── Create cluster ────────────────────────────────────────────────────────────
if k3d cluster list | grep -q "^$CLUSTER"; then
    echo "  Cluster $CLUSTER already exists — skipping creation."
else
    echo "  Creating k3d cluster: $CLUSTER"
    k3d cluster create "$CLUSTER" \
        --agents 1 \
        --no-lb \
        --k3s-arg "--disable=traefik@server:0" \
        --wait
    echo "  Cluster ready."
fi

kubectl config use-context "k3d-$CLUSTER"

# ── Namespace ─────────────────────────────────────────────────────────────────
kubectl apply -f "$MANIFESTS/00-namespace.yaml"

# ── Inject failures ───────────────────────────────────────────────────────────
echo ""
echo "  Injecting failure scenarios..."
kubectl apply -f "$MANIFESTS/01-crashloop.yaml"   # payment-service: CrashLoopBackOff
kubectl apply -f "$MANIFESTS/02-oom.yaml"          # analytics-worker: OOMKilled
kubectl apply -f "$MANIFESTS/04-imagepull.yaml"    # notification-svc: ImagePullBackOff
kubectl apply -f "$MANIFESTS/06-healthy.yaml"      # api-gateway: healthy baseline

echo ""
echo "  Waiting 30s for pods to enter failure states..."
sleep 30

echo ""
kubectl get pods -n "$NS" -o wide

# ── Observability stack ───────────────────────────────────────────────────────
echo ""
echo "  Deploying observability stack (Prometheus + Loki + Tempo + Alloy + OTel)..."
bash "$ROOT/demo/setup_observability.sh"
