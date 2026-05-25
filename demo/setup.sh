#!/usr/bin/env bash
# KubeVerdict demo setup
#
# What this does:
#   1. Detect / create a Kubernetes cluster (existing, k3d, or k3s)
#   2. Deploy demo charts via Helm (declared = "correct" state)
#   3. Apply drift patches to simulate real-world incidents
#   4. Init a local git repo → simulates a GitLab infrastructure repo
#   5. Write demo/.env.demo ready for run_rca.py
#
# Prerequisites: kubectl, helm, git  (+ k3d or k3s for cluster creation)
set -euo pipefail

NAMESPACE="kubeverdict-demo"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GITREPO_PATH="/tmp/kw-demo-gitops"
KUBECONFIG_PATH="${KUBECONFIG:-$HOME/.kube/config}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${GREEN}▶${NC} $*"; }

# ── Prerequisites ─────────────────────────────────────────────────────────────
step "Checking prerequisites"
command -v kubectl >/dev/null 2>&1 || error "kubectl not found"
command -v helm    >/dev/null 2>&1 || error "helm not found — install from https://helm.sh"
command -v git     >/dev/null 2>&1 || error "git not found"

# ── Cluster detection / creation ──────────────────────────────────────────────
step "Detecting Kubernetes cluster"

if kubectl cluster-info &>/dev/null 2>&1; then
  info "Using existing cluster"

elif command -v k3d &>/dev/null; then
  info "Creating k3d cluster 'kubeverdict-demo'..."
  k3d cluster create kubeverdict-demo \
    --agents 1 \
    --k3s-arg '--disable=traefik@server:*' \
    --wait
  KUBECONFIG_PATH="$(k3d kubeconfig write kubeverdict-demo)"
  export KUBECONFIG="$KUBECONFIG_PATH"

elif command -v k3s &>/dev/null; then
  info "Using local k3s..."
  KUBECONFIG_PATH="/etc/rancher/k3s/k3s.yaml"
  export KUBECONFIG="$KUBECONFIG_PATH"

else
  error "No cluster found. Install k3d (https://k3d.io) or run demo/k3s/install.sh first."
fi

# ── Context confirmation ──────────────────────────────────────────────────────
CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "(unknown)")
CURRENT_CLUSTER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null || echo "")
echo ""
echo -e "  ${YELLOW}Target context :${NC} $CURRENT_CONTEXT"
echo -e "  ${YELLOW}API server      :${NC} $CURRENT_CLUSTER"
echo -e "  ${YELLOW}Namespace       :${NC} $NAMESPACE"
echo ""
read -rp "  Deploy demo scenario to this cluster? [y/N] " _confirm
[[ "$_confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── Namespace ─────────────────────────────────────────────────────────────────
step "Creating namespace '$NAMESPACE'"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# ── Deploy charts via Helm ────────────────────────────────────────────────────
step "Deploying charts via Helm (declared / correct state)"

_helm_install() {
  local release="$1" chart="$2" wait_flag="${3:---wait --timeout 90s}"
  if helm status "$release" -n "$NAMESPACE" &>/dev/null; then
    info "  $release already installed — upgrading"
    helm upgrade "$release" "$chart" -n "$NAMESPACE" $wait_flag
  else
    info "  Installing $release"
    helm install "$release" "$chart" -n "$NAMESPACE" $wait_flag
  fi
}

_helm_install payment-service     "$SCRIPT_DIR/charts/payment-service"
_helm_install analytics-worker    "$SCRIPT_DIR/charts/analytics-worker"
# notification-service intentionally fails (missing ConfigMap + Secret) — skip readiness wait
_helm_install notification-service "$SCRIPT_DIR/charts/notification-service" "--rollback-on-failure=false"
# ml-inference image is patched to a broken registry after install — skip readiness wait
_helm_install ml-inference        "$SCRIPT_DIR/charts/ml-inference"         "--rollback-on-failure=false"

# Also deploy the healthy app and the GPU pending scenario via raw manifests
kubectl apply -f "$SCRIPT_DIR/manifests/05-pending.yaml"
kubectl apply -f "$SCRIPT_DIR/manifests/06-healthy.yaml"

# ── Introduce drift patches ───────────────────────────────────────────────────
# These simulate a developer/ops team making manual changes to production
# that were never committed back to the Helm chart values.
step "Applying drift patches (simulating manual prod changes)"

# Drift 1: analytics-worker — memory limit reduced from 512Mi → 50Mi
# Reason: "cost-cutting patch" that causes OOMKill
info "  Patch analytics-worker: limits.memory 512Mi → 50Mi (triggers OOMKill)"
kubectl patch pod analytics-worker -n "$NAMESPACE" \
  --type=json \
  -p='[{"op":"replace","path":"/spec/containers/0/resources/limits/memory","value":"50Mi"}]' \
  2>/dev/null || warn "  analytics-worker pod not found yet — drift will apply on restart"

# Drift 2: ml-inference — image patched to a private/broken registry
# Reason: "hotfix promotion" that referenced the wrong registry
info "  Patch ml-inference: image → myregistry.internal/ml-models/inference:v2.4.1-gpu"
kubectl set image deployment/ml-inference \
  model-server=myregistry.internal/ml-models/inference:v2.4.1-gpu \
  -n "$NAMESPACE" 2>/dev/null || true

# ── Init GitOps repo ──────────────────────────────────────────────────────────
# Simulates a GitLab infrastructure repository that KubeVerdict queries
# to detect drift between declared (git) and observed (cluster) state.
step "Initialising GitOps repo at $GITREPO_PATH"

rm -rf "$GITREPO_PATH"
mkdir -p "$GITREPO_PATH"
cd "$GITREPO_PATH"
git init -b main
git config user.email "demo@kubeverdict.local"
git config user.name "KubeVerdict Demo"

# Copy charts into the repo (this is the "source of truth")
mkdir -p charts
cp -r "$SCRIPT_DIR/charts/"* charts/

git add .
git commit -m "chore: initial chart state (correct values before drift)"

GITREPO_URL="file://$GITREPO_PATH"
cd "$SCRIPT_DIR"
info "  Git repo ready: $GITREPO_URL"

# ── Wait for pods to reach failure states ─────────────────────────────────────
step "Waiting for pods to settle (45 s)..."
sleep 45

step "Pod status in '$NAMESPACE'"
kubectl get pods -n "$NAMESPACE" -o wide

step "Helm releases"
helm list -n "$NAMESPACE"

# ── Create output directory ───────────────────────────────────────────────────
mkdir -p "$SCRIPT_DIR/output"

# ── Write .env.demo ───────────────────────────────────────────────────────────
step "Writing demo config → $SCRIPT_DIR/.env.demo"
cat > "$SCRIPT_DIR/.env.demo" <<EOF
# Auto-generated by demo/setup.sh
KUBECONFIG=${KUBECONFIG_PATH}
KUBE_NAMESPACES=${NAMESPACE}

# GitOps — points to our local simulated GitLab repo
GITOPS_ENABLED=true
GITOPS_REPO_URL=${GITREPO_URL}
GITOPS_BRANCH=main
GITOPS_CHARTS_PATH=charts

# metrics-server (included in k3s/k3d)
METRICS_SERVER_ENABLED=true

# Disable services not deployed in local demo
PROMETHEUS_ENABLED=false
OTEL_ENABLED=false
LOKI_ENABLED=false

# Mistral via Ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=mistral
OLLAMA_TIMEOUT=120

LOG_LEVEL=WARNING
EOF

echo ""
info "Setup complete."
echo ""
echo "  Incident scenarios deployed:"
echo "    payment-service      → CrashLoopBackOff  (DB unreachable)"
echo "    analytics-worker     → OOMKilled          (512Mi→50Mi drift patch)"
echo "    notification-service → ConfigError        (missing ConfigMap + Secret)"
echo "    ml-inference         → ImagePullBackOff   (broken image drift patch)"
echo "    gpu-worker           → Pending            (no GPU node)"
echo "    api-gateway          → Running ✓          (healthy baseline)"
echo ""
echo "  GitOps repo: $GITREPO_URL"
echo "    git log: $(git -C "$GITREPO_PATH" log --oneline)"
echo ""
echo "  Make sure Ollama + Mistral are running:"
echo "    bash demo/k3s/install_ollama.sh"
echo ""
echo "  Run the RCA:"
echo "    python demo/run_rca.py"
