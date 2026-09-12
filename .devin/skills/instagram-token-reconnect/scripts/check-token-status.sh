#!/usr/bin/env bash
# Check Instagram token status for all accounts.
# Usage: check-token-status.sh
set -euo pipefail

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

echo "=== Instagram Token Status ===" >&2

# Get all Instagram accounts and their token status
curl -s "http://localhost:8083/api/v1/accounts" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
accounts = d if isinstance(d, list) else d.get('accounts', d.get('data', []))
ig_accounts = [a for a in accounts if a.get('platform') == 'instagram']
if not ig_accounts:
    print('No Instagram accounts found')
    sys.exit(0)
for a in ig_accounts:
    meta = a.get('meta_data', {}) or {}
    print(f'')
    print(f'Account: {a.get(\"display_name\", a.get(\"username\", \"?\"))}')
    print(f'  ID: {a[\"id\"][:8]}...')
    print(f'  Has token: {bool(a.get(\"access_token_enc\"))}')
    print(f'  Token status: {meta.get(\"instagram_token_status\", \"unknown\")}')
    if meta.get(\"instagram_token_error\"):
        print(f'  Error: {meta[\"instagram_token_error\"][:100]}')
    if meta.get(\"instagram_token_expires_at\"):
        print(f'  Expires: {meta[\"instagram_token_expires_at\"][:19]}')
    if meta.get(\"instagram_token_refreshed_at\"):
        print(f'  Last refresh: {meta[\"instagram_token_refreshed_at\"][:19]}')
    if meta.get(\"instagram_token_checked_at\"):
        print(f'  Last check: {meta[\"instagram_token_checked_at\"][:19]}')
"

echo "" >&2
echo "=== Triggering token refresh task ===" >&2
docker compose exec -T social-worker-default celery -A app.worker.celery_app call \
  app.worker.tasks.instagram_token_refresh.refresh_instagram_tokens 2>&1 | head -2
