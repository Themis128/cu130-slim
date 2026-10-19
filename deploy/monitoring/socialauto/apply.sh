#!/bin/sh
# Deploy the SocialAuto metrics target + Grafana dashboard on the omv k3s
# monitoring stack. Run on omv:  ./apply.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
kubectl apply -f "$DIR/service.yaml" -f "$DIR/servicemonitor.yaml"
kubectl create configmap grafana-dashboard-social-platforms -n monitoring \
  --from-file=social-platforms.json="$DIR/dashboard-social-platforms.json" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring label configmap grafana-dashboard-social-platforms \
  grafana_dashboard=1 --overwrite
