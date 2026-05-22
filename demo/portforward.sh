#!/usr/bin/env bash
# KubeWhisperer — port-forward observability services to localhost.
# Run this in a separate terminal after setup_observability.sh.
#
# Exposes:
#   localhost:9090  → Prometheus
#   localhost:9093  → Alertmanager
#   localhost:3100  → Loki
#   localhost:3200  → Tempo
#
# Usage:
#   bash demo/portforward.sh
#   bash demo/portforward.sh --stop
set -euo pipefail

OBS_NS="observability"
PF_PIDS_FILE="/tmp/kubewhisperer-portforward.pids"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}  ✓${NC}  $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }

stop_all() {
  if [[ -f "$PF_PIDS_FILE" ]]; then
    while read -r pid; do
      kill "$pid" 2>/dev/null && echo "  killed $pid" || true
    done < "$PF_PIDS_FILE"
    rm -f "$PF_PIDS_FILE"
    echo "  All port-forwards stopped."
  else
    warn "No port-forward PID file found."
  fi
}

if [[ "${1:-}" == "--stop" ]]; then
  stop_all
  exit 0
fi

# Stop any existing forwards first
stop_all 2>/dev/null || true
> "$PF_PIDS_FILE"

pf() {
  local svc=$1 local_port=$2 remote_port=$3 label=$4
  kubectl port-forward "svc/$svc" "${local_port}:${remote_port}" \
    -n "$OBS_NS" >/dev/null 2>&1 &
  echo $! >> "$PF_PIDS_FILE"
  info "$label → http://localhost:$local_port"
}

echo ""
echo "  Starting port-forwards..."
echo ""

pf "kube-prometheus-stack-prometheus"    9090 9090 "Prometheus  "
pf "kube-prometheus-stack-alertmanager"  9093 9093 "Alertmanager"
pf "loki"                                3100 3100 "Loki        "
pf "tempo"                               3200 3200 "Tempo       "

cat <<EOF

  PIDs saved to $PF_PIDS_FILE
  Stop with:  bash demo/portforward.sh --stop
  Press Ctrl-C to exit (forwards continue in background).
EOF

# Keep running so Ctrl-C is intuitive
wait
