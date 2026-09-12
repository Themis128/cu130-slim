#!/usr/bin/env bash
# Verify that the Instagram private API session is active by calling
# the sidecar's profile endpoint.
#
# Usage:
#   verify.sh <account_id>
set -euo pipefail

ACCOUNT_ID="${1:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id>" >&2
  exit 1
fi

SIDECAR="http://localhost:8011"

# Get the session_id from the database
SESSION_ID=$(docker compose exec -T social-postgres psql -U social_user -d social_automation -t -c "
SELECT meta_data->>'private_api_session_id'
FROM social_accounts
WHERE id = '${ACCOUNT_ID}';
" 2>/dev/null | tr -d ' \n')

if [[ -z "$SESSION_ID" || "$SESSION_ID" == "null" ]]; then
  echo "❌ No session_id found for account $ACCOUNT_ID" >&2
  echo "Run login.sh first to create a session." >&2
  exit 1
fi

echo "=== Verifying session ===" >&2

# Get the profile from the sidecar
RESULT=$(curl -s "${SIDECAR}/auth/profile" \
  -H "X-Session-ID: ${SESSION_ID}" 2>/dev/null)

USERNAME=$(echo "$RESULT" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('username', d.get('user', {}).get('username', '')))
except:
    print('')
" 2>/dev/null)

if [[ -n "$USERNAME" ]]; then
  echo "✅ Session active for @$USERNAME" >&2
  exit 0
else
  echo "❌ Session not active or expired" >&2
  echo "$RESULT" | head -5 >&2
  exit 1
fi
