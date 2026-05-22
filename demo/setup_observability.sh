#!/usr/bin/env bash
# KubeWhisperer — observability stack setup
#
# Deploys on the k3d demo cluster:
#   • kube-prometheus-stack  (Prometheus + Alertmanager + kube-state-metrics)
#   • Grafana Loki           (pod log aggregation)
#   • Grafana Tempo          (OTel trace storage)
#   • Grafana Alloy          (log collection agent — DaemonSet)
#   • OTel Collector         (OTLP receiver → Tempo)
#   • trace-emitter workload (synthetic OTel traces for failing services)
#
# Alertmanager is configured to POST firing K8s alerts to KubeWhisperer.
#
# Usage:
#   bash demo/setup_observability.sh
#   bash demo/setup_observability.sh --teardown
#   KUBEWHISPERER_WEBHOOK_URL=http://1.2.3.4:8000/... bash demo/setup_observability.sh
#
# Prerequisites: k3d, helm, kubectl
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OBS_DIR="$ROOT/demo/observability"
MANIFESTS_DIR="$ROOT/demo/manifests"

OBS_NS="observability"
DEMO_NS="kubewhisperer-demo"

# URL Alertmanager uses to reach KubeWhisperer running on the host machine.
# host.k3d.internal resolves to the Docker host from inside the k3d cluster.
KUBEWHISPERER_WEBHOOK_URL="${KUBEWHISPERER_WEBHOOK_URL:-http://host.k3d.internal:8000/api/v1/webhook/alertmanager}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}  ✓${NC}  $*"; }
step()  { echo -e "\n${CYAN}──${NC}  $*"; }
warn()  { echo -e "${YELLOW}  ⚠${NC}  $*"; }

# ── Teardown ──────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--teardown" ]]; then
  step "Tearing down observability stack..."
  helm uninstall kube-prometheus-stack -n "$OBS_NS" 2>/dev/null || true
  helm uninstall loki               -n "$OBS_NS" 2>/dev/null || true
  helm uninstall tempo              -n "$OBS_NS" 2>/dev/null || true
  helm uninstall alloy              -n "$OBS_NS" 2>/dev/null || true
  kubectl delete -f "$OBS_DIR/otel-collector.yaml"  2>/dev/null || true
  kubectl delete -f "$MANIFESTS_DIR/07-trace-emitter.yaml" 2>/dev/null || true
  kubectl delete namespace "$OBS_NS" 2>/dev/null || true
  info "Done."
  exit 0
fi

# ── Prerequisites ─────────────────────────────────────────────────────────────
step "Checking prerequisites..."
for cmd in helm kubectl k3d; do
  command -v "$cmd" &>/dev/null || { echo "  ERROR: $cmd not found"; exit 1; }
done
info "helm $(helm version --short), kubectl $(kubectl version --client -o json | python3 -c 'import sys,json; print(json.load(sys.stdin)["clientVersion"]["gitVersion"])')"

# ── Namespace ─────────────────────────────────────────────────────────────────
step "Creating namespace $OBS_NS..."
kubectl create namespace "$OBS_NS" --dry-run=client -o yaml | kubectl apply -f -
info "Namespace ready."

# ── Helm repos ────────────────────────────────────────────────────────────────
step "Adding Helm repos..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>/dev/null || true
helm repo add grafana              https://grafana.github.io/helm-charts              2>/dev/null || true
helm repo update --fail-on-repo-update-fail
info "Repos updated."

# ── kube-prometheus-stack ─────────────────────────────────────────────────────
step "Deploying kube-prometheus-stack..."
# Inject the webhook URL into the values via env substitution
VALUES_PROM=$(mktemp /tmp/kube-prometheus-stack-XXXXXX.yaml)
trap "rm -f $VALUES_PROM" EXIT
sed "s|\${KUBEWHISPERER_WEBHOOK_URL}|${KUBEWHISPERER_WEBHOOK_URL}|g" \
  "$OBS_DIR/kube-prometheus-stack-values.yaml" > "$VALUES_PROM"

helm upgrade --install kube-prometheus-stack \
  prometheus-community/kube-prometheus-stack \
  --namespace "$OBS_NS" \
  --values "$VALUES_PROM" \
  --set prometheus.prometheusSpec.retention=1d \
  --wait --timeout 5m
info "Prometheus + Alertmanager ready."
info "Alertmanager webhook → $KUBEWHISPERER_WEBHOOK_URL"

# ── Loki ─────────────────────────────────────────────────────────────────────
step "Deploying Loki..."
helm upgrade --install loki \
  grafana/loki \
  --namespace "$OBS_NS" \
  --values "$OBS_DIR/loki-values.yaml" \
  --wait --timeout 3m
info "Loki ready."

# ── Tempo ─────────────────────────────────────────────────────────────────────
step "Deploying Tempo..."
helm upgrade --install tempo \
  grafana/tempo \
  --namespace "$OBS_NS" \
  --values "$OBS_DIR/tempo-values.yaml" \
  --wait --timeout 3m
info "Tempo ready."

# ── Grafana Alloy (log collection) ────────────────────────────────────────────
step "Deploying Grafana Alloy..."
helm upgrade --install alloy \
  grafana/alloy \
  --namespace "$OBS_NS" \
  --values "$OBS_DIR/alloy-values.yaml" \
  --wait --timeout 3m
info "Alloy DaemonSet ready — scraping pod logs."

# ── OTel Collector ────────────────────────────────────────────────────────────
step "Deploying OTel Collector..."
kubectl apply -f "$OBS_DIR/otel-collector.yaml"
kubectl rollout status deployment/otel-collector -n "$OBS_NS" --timeout=120s
info "OTel Collector ready (OTLP gRPC :4317, HTTP :4318)."

# ── Trace emitter ─────────────────────────────────────────────────────────────
step "Deploying trace emitter (payment-service + analytics-worker OTel)..."
kubectl apply -f "$MANIFESTS_DIR/07-trace-emitter.yaml"
info "Trace emitter deployed — will start emitting after pip install (~30s)."

# ── Summary ───────────────────────────────────────────────────────────────────
cat <<EOF

${GREEN}════════════════════════════════════════════════════════════════${NC}
  Observability stack deployed.

  Run port-forwards to expose services locally:
    bash demo/portforward.sh

  Then update demo/.env.demo:
    PROMETHEUS_ENABLED=true    PROMETHEUS_URL=http://localhost:9090
    OTEL_ENABLED=true          OTEL_BACKEND_TYPE=tempo
    OTEL_BACKEND_URL=http://localhost:3200
    LOKI_ENABLED=true          LOKI_URL=http://localhost:3100
    ALERTMANAGER_WEBHOOK_URL=$KUBEWHISPERER_WEBHOOK_URL

  Start KubeWhisperer API (receives Alertmanager webhooks):
    uvicorn api.app:app --host 0.0.0.0 --port 8000
${GREEN}════════════════════════════════════════════════════════════════${NC}
EOF
