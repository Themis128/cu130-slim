#!/usr/bin/env bash
# Verify that social.cloudless.gr is reachable and serving media files
# via the /api/v1/media/view endpoint (required for TikTok PULL_FROM_URL).
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

echo "=== Tunnel URL ==="
TUNNEL_URL=$(docker compose exec -T social-api cat /run/tunnel/url 2>/dev/null || echo "")
if [ -z "$TUNNEL_URL" ]; then
  echo "No tunnel URL found. Cloudflare tunnel may not be running."
  exit 1
fi
echo "Tunnel: $TUNNEL_URL"

echo ""
echo "=== Health Check ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$TUNNEL_URL/health")
echo "GET $TUNNEL_URL/health → $HTTP_CODE"

echo ""
echo "=== Media URL Test ==="
# Get the first media asset's storage_path
STORAGE_PATH=$(curl -sf -H "Authorization: Bearer $TOKEN" "$API/api/v1/media/assets?limit=1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); items=d if isinstance(d,list) else d.get('items',d.get('assets',[])); print(items[0]['storage_path'] if items else '')")

if [ -z "$STORAGE_PATH" ]; then
  echo "No media assets found in the library."
  exit 1
fi

MEDIA_URL="$TUNNEL_URL/api/v1/media/view?path=$STORAGE_PATH"
echo "Testing: $MEDIA_URL"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$MEDIA_URL")
SIZE=$(curl -s -o /dev/null -w "%{size_download}" "$MEDIA_URL")
echo "  HTTP $HTTP_CODE | Size: ${SIZE} bytes"

if [ "$HTTP_CODE" = "200" ] && [ "$SIZE" -gt 100 ]; then
  echo ""
  echo "✓ Media URL is publicly reachable."
  echo "  TikTok PULL_FROM_URL will work IF the domain is verified."
  echo "  Check verification status at:"
  echo "  https://developers.tiktok.com → Cloudless app → URL properties"
else
  echo ""
  echo "✗ Media URL is NOT reachable or returned an error."
  echo "  Check Cloudflare tunnel and social-api container."
fi

echo ""
echo "=== HTTPS / No-Redirect Check ==="
REDIRECT=$(curl -s -o /dev/null -w "%{redirect_url}" "$MEDIA_URL" 2>/dev/null)
if [ -z "$REDIRECT" ] || [ "$REDIRECT" = "$MEDIA_URL" ]; then
  echo "✓ No redirect — TikTok requirement satisfied."
else
  echo "✗ URL redirects to: $REDIRECT"
  echo "  TikTok requires media URLs that do not redirect."
fi
