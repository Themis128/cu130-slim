#!/usr/bin/env bash
# Sync brand bio to all connected platforms.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# Get all connected platforms
PLATFORMS=$(curl -sf "$API/api/v1/accounts" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
accounts = data if isinstance(data, list) else data.get('accounts', data.get('data', []))
seen = set()
for a in accounts:
    p = a['platform']
    if p not in seen:
        seen.add(p)
        print(p)
")

echo "=== Syncing brand bio to all platforms ==="
echo ""

for PLATFORM in $PLATFORMS; do
  echo "--- $PLATFORM ---"
  bash "$ROOT/.devin/skills/brand-sync/scripts/sync-platform.sh" "$PLATFORM" 2>&1 || echo "  Failed: $PLATFORM"
  echo ""
done

echo "=== All platforms processed ==="
