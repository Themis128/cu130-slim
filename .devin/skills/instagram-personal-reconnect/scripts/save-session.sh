#!/usr/bin/env bash
# Save the instagrapi session to SocialAuto's database so it persists
# across restarts and is available to the polling/publishing tasks.
#
# Usage:
#   save-session.sh <account_id> <session_id>
set -euo pipefail

ACCOUNT_ID="${1:-}"
SESSION_ID="${2:-}"

if [[ -z "$ACCOUNT_ID" || -z "$SESSION_ID" ]]; then
  echo "Usage: $0 <account_id> <session_id>" >&2
  exit 1
fi

SIDECAR="http://localhost:8011"

cd /home/tbaltzakis/cu130-slim
set +u; source .env 2>/dev/null || true; set -u

TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
  echo "❌ Could not authenticate" >&2
  exit 1
fi

# Get settings from the sidecar
SETTINGS=$(curl -s "${SIDECAR}/auth/settings" \
  -H "X-Session-ID: ${SESSION_ID}" 2>/dev/null)

SETTINGS_JSON=$(echo "$SETTINGS" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    # The settings endpoint returns the settings JSON
    if isinstance(d, dict):
        if 'settings' in d:
            print(json.dumps(d['settings']))
        else:
            print(json.dumps(d))
    else:
        print(json.dumps(d))
except:
    print('{}')
" 2>/dev/null || echo "{}")

# Save to SocialAuto via the database
# Update the account's meta_data with the session info
docker compose exec -T social-postgres psql -U social_user -d social_automation -c "
UPDATE social_accounts
SET meta_data = meta_data || jsonb_build_object(
  'private_api_session_id', '${SESSION_ID}',
  'private_api_settings', '${SETTINGS_JSON}'::jsonb,
  'private_api_connected_at', now()::text
)
WHERE id = '${ACCOUNT_ID}';
" 2>/dev/null

echo "✅ Session saved to SocialAuto for account $ACCOUNT_ID" >&2
echo "  session_id: ${SESSION_ID:0:8}..." >&2
echo "  settings: $(echo "$SETTINGS_JSON" | wc -c) bytes" >&2
