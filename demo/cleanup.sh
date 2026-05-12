#!/usr/bin/env bash
# Tear down the KubeWhisperer demo
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Deleting namespace kubewhisperer-demo..."
kubectl delete namespace kubewhisperer-demo --ignore-not-found

if command -v k3d &>/dev/null && k3d cluster list 2>/dev/null | grep -q "kubewhisperer-demo"; then
  echo "Deleting k3d cluster..."
  k3d cluster delete kubewhisperer-demo
fi

rm -f "$SCRIPT_DIR/.env.demo"
echo "Done."
