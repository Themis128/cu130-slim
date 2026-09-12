#!/usr/bin/env bash
# Validate a specific Instagram account's token via the Graph API.
# Usage: validate-token.sh <account_id>
set -euo pipefail

ACCOUNT_ID="${1:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id>" >&2
  exit 1
fi

cd /home/tbaltzakis/cu130-slim
source .env 2>/dev/null || true

TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
  echo "Error: Could not authenticate with SocialAuto" >&2
  exit 1
fi

echo "=== Validating Instagram token for account $ACCOUNT_ID ===" >&2

# Trigger the token refresh task which validates all tokens
docker compose exec -T social-worker-default celery -A app.worker.celery_app call \
  app.worker.tasks.instagram_token_refresh.refresh_instagram_tokens 2>&1 | head -2

sleep 10

# Check the updated status
curl -s "http://localhost:8083/api/v1/accounts" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
accounts = d if isinstance(d, list) else d.get('accounts', d.get('data', []))
for a in accounts:
    if a.get('platform') == 'instagram' and str(a['id']).startswith('$ACCOUNT_ID'[:8]):
        meta = a.get('meta_data', {}) or {}
        status = meta.get('instagram_token_status', 'unknown')
        print(f'Status: {status}')
        if meta.get('instagram_token_error'):
            print(f'Error: {meta[\"instagram_token_error\"][:200]}')
        if meta.get('instagram_token_checked_at'):
            print(f'Checked: {meta[\"instagram_token_checked_at\"][:19]}')
        if status == 'valid':
            print('✅ Token is valid')
        elif status == 'refreshed':
            print('✅ Token was refreshed successfully')
        else:
            print('❌ Token needs reconnection — use reconnect-oauth.sh')
"
