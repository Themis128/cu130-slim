#!/usr/bin/env bash
# Import + publish SocialAuto daily Slack digest workflow, then restart n8n.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

WF="n8n-workflows/socialauto-daily-slack-digest.json"
ID="socialauto-daily-slack-digest"

[[ -f "$WF" ]] || { echo "Missing $WF" >&2; exit 1; }

docker cp "$WF" n8n:/tmp/socialauto-daily-slack-digest.json
docker exec n8n n8n import:workflow --input=/tmp/socialauto-daily-slack-digest.json
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
echo "Deployed. Manual: POST http://localhost:5678/webhook/socialauto-daily-digest"
echo "Schedule: daily 09:00 Europe/Athens → POST /api/v1/ops/daily-digest → Slack #socialauto"
echo "Requires SLACK_WEBHOOK_URL (or SLACK_BOT_TOKEN) in .env for social-api/social-worker"
