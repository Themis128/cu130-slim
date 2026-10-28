#!/bin/sh
# Deploy the n8n metrics target on the omv k3s monitoring stack.
# Run on omv:  ./apply.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
kubectl apply -f "$DIR/service.yaml" -f "$DIR/servicemonitor.yaml"
