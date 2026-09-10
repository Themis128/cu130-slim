#!/usr/bin/env bash
# Check Messenger sidecar health + stats
set -euo pipefail

echo "=== Sidecar Health ==="
curl -s http://localhost:9230/health | python3 -m json.tool

echo ""
echo "=== Sidecar Stats ==="
curl -s http://localhost:9230/stats | python3 -m json.tool

echo ""
echo "=== Sidecar Container Status ==="
docker ps --filter name=messenger-sidecar --format "{{.Status}}" 2>/dev/null || echo "Docker not available"
