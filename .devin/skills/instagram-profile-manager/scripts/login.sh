#!/usr/bin/env bash
# Login to Instagram sidecar using username and password
# Usage: login.sh [username] [password]
# Reads from social_secrets DB if not provided
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${INSTAGRAM_SIDECAR_URL:-http://localhost:8011}"

USERNAME="${1:-}"
PASSWORD="${2:-}"

# If not provided, read from DB
if [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
  CREDS=$(docker compose exec -T social-api python3 -c "
import asyncio
from app.db.session import engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def main():
    async with AsyncSession(engine) as db:
        result = await db.execute(text(\"SELECT key, value FROM social_secrets WHERE key IN ('INSTAGRAM_USERNAME', 'INSTAGRAM_PASSWORD')\"))
        creds = {row[0]: row[1] for row in result.fetchall()}
        print(f\"{creds.get('INSTAGRAM_USERNAME','')}\t{creds.get('INSTAGRAM_PASSWORD','')}\")

asyncio.run(main())
" 2>/dev/null | tail -1)
  USERNAME=$(echo "$CREDS" | cut -f1)
  PASSWORD=$(echo "$CREDS" | cut -f2)
fi

if [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
  echo "No Instagram credentials found. Provide username and password as args or store in social_secrets."
  exit 1
fi

echo "Logging in to Instagram as $USERNAME..."

RESP=$(curl -sf -X POST "$API/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$USERNAME" \
  --data-urlencode "password=$PASSWORD" \
  --data-urlencode "locale=el_GR" \
  --data-urlencode "timezone=10800" 2>&1)

echo "$RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    sid = d.get('session_id') or d.get('sessionid') or 'N/A'
    print(f'Session ID: {sid}')
    if d.get('two_factor_required'):
        print('2FA required. Use login-2fa.sh with verification code.')
    if d.get('challenge_required'):
        print('Challenge required. Use challenge-resolve.sh with security code.')
    user = d.get('user', {})
    if user:
        print(f'Username: {user.get(\"username\", \"N/A\")}')
except:
    print(sys.stdin.read())
"
