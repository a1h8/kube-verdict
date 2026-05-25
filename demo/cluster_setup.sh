#!/usr/bin/env bash
# KubeVerdict — demo failure injection
#
# Stages a live incident on the k0rdent cluster:
#   --baseline   deploy healthy api-gateway (contrast / before state)
#   --inject     inject payment-service CrashLoop + analytics-worker OOM
#   --reset      delete all demo workloads
#   (no flag)    baseline only, then prompts for --inject
#
# Usage:
#   bash demo/cluster_setup.sh --baseline
#   bash demo/cluster_setup.sh --inje#   bash demo/cluster_setup.sh --fix
#   bash demo/cluster_setup.sh --reset
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFESTS="$ROOT/demo/manifests"
NS="kubeverdict-demo"
CTX="k3d-k0rdent"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}  ✓${NC}  $*"; }
step()  { echo -e "\n${CYAN}──${NC}  $*"; }
warn()  { echo -e "${YELLOW}  ⚠${NC}  $*"; }

# ── Context guard ─────────────────────────────────────────────────────────────
current_ctx=$(kubectl config current-context 2>/dev/null || echo "none")
if [[ "$current_ctx" != "$CTX" ]]; then
  warn "Switching context to $CTX (was: $current_ctx)"
  kubectl config use-context "$CTX"
fi

MODE="${1:-all}"

# ── Reset ─────────────────────────────────────────────────────────────────────
if [[ "$MODE" == "--reset" ]]; then
  step "Resetting demo namespace..."
  kubectl delete all --all -n "$NS" 2>/dev/null || true
  kubectl delete pod --all -n "$NS" --grace-period=0 --force 2>/dev/null || true
  kubectl wait --for=delete pod --all -n "$NS" --timeout=30s 2>/dev/null || true
  info "Namespace $NS is clean."
  exit 0
fi

# ── Baseline: healthy api-gateway ─────────────────────────────────────────────
_baseline() {
  step "Deploying healthy baseline (api-gateway)..."
  kubectl apply -f "$MANIFESTS/06-healthy.yaml"
  kubectl rollout status deployment/api-gateway -n "$NS" --timeout=120s
  info "api-gateway Running — 2/2 pods healthy."
  echo ""
  kubectl get pods -n "$NS"
}

# ── Inject: payment-service CrashLoop + analytics-worker OOM ─────────────────
_inject() {
  step "Injecting failures..."
  kubectl delete pod analytics-worker -n "$NS" --grace-period=0 --force 2>/dev/null || true
  kubectl apply -f "$MANIFESTS/01-crashloop.yaml"   # payment-service: DB unreachable → exit 1
  kubectl apply -f "$MANIFESTS/02-oom.yaml"          # analytics-worker: 200MiB alloc > 50MiB limit
  info "Manifests applied — pods entering failure state..."
  echo ""
  echo -e "${YELLOW}  Watch pods:${NC}  kubectl get pods -n $NS -w"
  echo -e "${YELLOW}  Watch logs:${NC}  kubectl logs -n $NS -l app=payment-service --tail=10 -f"
  echo ""
  echo "  Alertmanager will fire KubePodCrashLooping + KubeDeploymentReplicasMismatch"
  echo "  KubeVerdict webhook → RCA in ~30s"
}

# ── Fix: apply remediation manifests ─────────────────────────────────────────
_fix() {
  step "Applying remediation..."
  kubectl delete pod analytics-worker -n "$NS" 2>/dev/null || true
  kubectl apply -f "$MANIFESTS/08-fix.yaml"
  kubectl rollout status deployment/db-primary -n "$NS" --timeout=60s
  kubectl rollout status deployment/payment-service -n "$NS" --timeout=120s
  info "db-primary up — payment-service reconnected."
  echo ""
  kubectl get pods -n "$NS"
}

case "$MODE" in
  --baseline) _baseline ;;
  --inject)   _inject ;;
  --fix)      _fix ;;
  *)
    _baseline
    echo ""
    warn "Run 'bash demo/cluster_setup.sh --inject' when ready to trigger the incident."
    ;;
esac
