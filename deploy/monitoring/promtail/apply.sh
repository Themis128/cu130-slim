#!/usr/bin/env bash
# Apply the promtail log shipper to the monitoring namespace.
# Run on omv:  sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml apply -f promtail.yaml
set -euo pipefail
cd "$(dirname "$0")"
KUBECTL="${KUBECTL:-kubectl}"
$KUBECTL apply -f promtail.yaml
$KUBECTL -n monitoring rollout status deployment/promtail --timeout=120s
echo "verify: curl -s http://192.168.1.128:30100/loki/api/v1/label/namespace/values"
