#!/usr/bin/env bash
# Import + publish Cloudless carousel workflow via n8n CLI, then restart.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

WF="n8n-workflows/cloudless-carousel-pipeline.json"
ID="cloudless-cf-carousel-linkedin"

[[ -f "$WF" ]] || { echo "Missing $WF" >&2; exit 1; }

docker cp "$WF" n8n:/tmp/cloudless-carousel-pipeline.json
docker exec n8n n8n import:workflow --input=/tmp/cloudless-carousel-pipeline.json
docker exec n8n n8n publish:workflow --id="$ID"
docker compose restart n8n

for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:5678/healthz >/dev/null; then
    echo "n8n healthy"
    break
  fi
  sleep 2
done

docker exec n8n n8n list:workflow --active=true
echo "Deployed. Webhook: POST http://localhost:5678/webhook/cloudless-carousel"
