#!/usr/bin/env bash
# KubeWhisperer — k3d cluster initialisation
#
# Creates (or reuses) a k3d cluster, avoids port conflicts with
# other running k3d clusters / Rancher Desktop, and creates
# the required namespaces.
#
# Usage:
#   bash demo/k3d_init.sh                         # create default cluster
#   bash demo/k3d_init.sh --cluster my-cluster    # custom cluster name
#   bash demo/k3d_init.sh --agents 2              # extra worker nodes
#   bash demo/k3d_init.sh --reset                 # delete and recreate
#   bash demo/k3d_init.sh --delete                # delete only
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
CLUSTER="${CLUSTER:-kubewhisperer-demo}"
AGENTS="${AGENTS:-1}"
RESET=false
DELETE_ONLY=false

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}  ✓${NC}  $*"; }
step()  { echo -e "\n${CYAN}──${NC}  $*"; }
warn()  { echo -e "${YELLOW}  ⚠${NC}  $*"; }
die()   { echo -e "${RED}  ✗${NC}  $*" >&2; exit 1; }

# ── Args ──────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cluster) CLUSTER="$2"; shift 2 ;;
    --agents)  AGENTS="$2";  shift 2 ;;
    --reset)   RESET=true;   shift ;;
    --delete)  DELETE_ONLY=true; shift ;;
    *) die "Unknown argument: $1" ;;
  esac
done

# ── Prerequisites ─────────────────────────────────────────────────────────────
step "Checking prerequisites..."
for cmd in k3d kubectl helm docker; do
  command -v "$cmd" &>/dev/null || die "$cmd not found — install it first"
done
docker info &>/dev/null || die "Docker is not running"
info "k3d $(k3d version | head -1 | awk '{print $3}'), kubectl $(kubectl version --client -o json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)["clientVersion"]["gitVersion"])' 2>/dev/null || echo '?')"

# ── Delete ────────────────────────────────────────────────────────────────────
if $DELETE_ONLY || $RESET; then
  step "Deleting cluster '$CLUSTER'..."
  k3d cluster delete "$CLUSTER" 2>/dev/null && info "Deleted." || warn "Cluster not found — nothing to delete."
  $DELETE_ONLY && exit 0
fi

# ── Corporate CA injection (TLS-intercepting proxy support) ──────────────────
# Extracts trusted CA certs from the macOS keychain that are needed by
# k3d container nodes (e.g. a TLS-intercepting proxy is present).
_inject_ca_args=""
if [[ "$(uname -s)" == "Darwin" ]] && command -v security &>/dev/null; then
  CA_BUNDLE=/tmp/k3d-corp-ca.pem
  security find-certificate -a -p /Library/Keychains/System.keychain 2>/dev/null \
    | python3 -c "
import sys, subprocess, re
pem = sys.stdin.read()
certs = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', pem, re.DOTALL)
found = []
for c in certs:
    r = subprocess.run(['openssl','x509','-noout','-subject'], input=c.encode(), capture_output=True)
    if any(k in r.stdout.decode().lower() for k in ['zscaler','bluecoat']):
        found.append(c)
if found:
    print('\n'.join(found))
" 2>/dev/null > "$CA_BUNDLE"
  if [[ -s "$CA_BUNDLE" ]]; then
    warn "Corporate TLS proxy CA detected — injecting into k3d nodes"
    _inject_ca_args="--volume ${CA_BUNDLE}:/etc/ssl/certs/corp-proxy-ca.pem@server:*;agent:*"
  fi
fi

# ── Find free API port ────────────────────────────────────────────────────────
_port_free() {
  ! lsof -iTCP:"$1" -sTCP:LISTEN &>/dev/null 2>&1
}

_pick_api_port() {
  for port in 6551 6552 6553 6554 6555 7550 7551; do
    _port_free "$port" && echo "$port" && return
  done
  die "No free port found for k3d API server (tried 6551-6555, 7550-7551)"
}

# ── Create cluster ────────────────────────────────────────────────────────────
if k3d cluster list 2>/dev/null | grep -q "^${CLUSTER}[[:space:]]"; then
  info "Cluster '$CLUSTER' already exists — skipping creation."
  kubectl config use-context "k3d-${CLUSTER}"
else
  API_PORT=$(_pick_api_port)
  step "Creating k3d cluster '$CLUSTER' (API port: $API_PORT, agents: $AGENTS)..."
  # shellcheck disable=SC2086
  k3d cluster create "$CLUSTER" \
    --api-port "$API_PORT" \
    --agents "$AGENTS" \
    --k3s-arg "--disable=traefik@server:0" \
    $_inject_ca_args \
    --wait
  info "Cluster created."
fi

# ── Merge kubeconfig ──────────────────────────────────────────────────────────
step "Merging kubeconfig..."
k3d kubeconfig merge "$CLUSTER" --kubeconfig-merge-default &>/dev/null
kubectl config use-context "k3d-${CLUSTER}"
info "Context: k3d-${CLUSTER}"

# ── Wait for node ready ───────────────────────────────────────────────────────
step "Waiting for nodes..."
kubectl wait --for=condition=Ready node --all --timeout=90s
kubectl get nodes -o wide
info "All nodes ready."

# ── Pause image pre-load (corporate TLS proxy) ───────────────────────────────
if [[ -n "$_inject_ca_args" ]]; then
  step "Pre-loading pause image (corporate proxy bypass)..."
  docker pull rancher/mirrored-pause:3.6 -q 2>&1 | tail -1
  k3d image import rancher/mirrored-pause:3.6 -c "$CLUSTER" 2>&1 | tail -2
  info "Pause image imported."
fi

# ── Namespaces ────────────────────────────────────────────────────────────────
step "Creating namespaces..."
for ns in observability kubewhisperer-demo; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
  info "namespace/$ns"
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo "  Cluster : k3d-${CLUSTER}"
echo "  Nodes   : $(kubectl get nodes --no-headers | wc -l | tr -d ' ')"
echo "  Context : $(kubectl config current-context)"
echo ""
echo "  Next steps:"
echo "    bash demo/setup_observability.sh    # Prometheus + Loki + Tempo + OTel"
echo "    bash demo/cluster_setup.sh          # inject failure scenarios"
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo ""
