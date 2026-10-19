#!/bin/sh
# Deploy the EspoCRM metrics exporter on the omv k3s monitoring stack.
# Run on omv:  ./apply.sh   (requires kubectl access; reuses the existing
# espocrm-secrets/mariadb-password secret — nothing to create out-of-band)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
kubectl create configmap espocrm-exporter -n espocrm \
  --from-file=exporter.py="$DIR/exporter.py" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$DIR/deployment.yaml" -f "$DIR/service.yaml" -f "$DIR/servicemonitor.yaml"
kubectl rollout status deployment/espocrm-exporter -n espocrm --timeout=120s
kubectl create configmap grafana-dashboard-espocrm-vitals -n monitoring \
  --from-file=espocrm-vitals.json="$DIR/dashboard-espocrm-vitals.json" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring label configmap grafana-dashboard-espocrm-vitals \
  grafana_dashboard=1 --overwrite
