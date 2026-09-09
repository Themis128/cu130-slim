#!/usr/bin/env bash
# Upload logo as profile picture to all supported platforms.
# Usage: sync-profile-pic.sh [logo_path]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
LOGO="${1:-}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# Get logo from brand profile if not provided
if [ -z "$LOGO" ]; then
  LOGO_URL=$(curl -sf "$API/api/v1/brand" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
v = d.get('visual') or {}
url = v.get('logo_url', '')
# Extract path from URL
if 'path=' in url:
    print(url.split('path=')[1])
")
  if [ -n "$LOGO_URL" ]; then
    # Download logo from media library
    curl -sf -o /tmp/brand-logo.png "$API/api/v1/media/view?path=$LOGO_URL" 2>/dev/null
    LOGO="/tmp/brand-logo.png"
  fi
fi

if [ -z "$LOGO" ] || [ ! -f "$LOGO" ]; then
  echo "No logo found. Provide a path or set logo in brand profile."
  exit 1
fi

echo "=== Using logo: $LOGO ==="
file "$LOGO"
echo ""

# Get all accounts
ACCOUNTS=$(curl -sf "$API/api/v1/accounts" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
accounts = data if isinstance(data, list) else data.get('accounts', data.get('data', []))
for a in accounts:
    print(f\"{a['id']}|{a['platform']}|{a.get('username','')}\")
")

echo "=== Uploading profile picture to all platforms ==="
echo ""

while IFS='|' read -r ACCOUNT_ID PLATFORM USERNAME; do
  echo "--- $PLATFORM ($USERNAME) ---"
  case "$PLATFORM" in
    instagram|facebook|linkedin|twitter)
      # Use SocialAuto profile API
      curl -sf -X POST "$API/api/v1/profile/$ACCOUNT_ID/picture" \
        -H "Authorization: Bearer $TOKEN" \
        -F "file=@$LOGO" \
        | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d.get('success'):
    print(f'  OK: {d.get(\"updated_fields\", [])}')
else:
    print(f'  Failed: {d.get(\"detail\", d.get(\"message\", \"unknown\"))}')
" 2>/dev/null || echo "  Failed: $PLATFORM does not support picture upload via API"
      ;;
    threads)
      echo "  Skipped: Threads profile pic synced from Instagram"
      ;;
    tiktok)
      echo "  Skipped: TikTok does not support picture upload via API"
      ;;
    *)
      echo "  Skipped: $PLATFORM not supported"
      ;;
  esac
  echo ""
done <<< "$ACCOUNTS"

echo "=== Done ==="
