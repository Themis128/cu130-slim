#!/bin/sh
# Deploy the Cloudflare metrics collector on the omv k3s monitoring stack.
# Run on omv:  ./apply.sh   (requires kubectl access + the
# cloudflare-exporter secret already created — see README.md)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
kubectl create configmap cloudflare-exporter -n monitoring \
  --from-file=collector.py="$DIR/collector.py" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$DIR/deployment.yaml" -f "$DIR/service.yaml" -f "$DIR/servicemonitor.yaml"
kubectl rollout status deployment/cloudflare-exporter -n monitoring --timeout=120s
