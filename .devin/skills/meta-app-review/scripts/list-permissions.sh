#!/usr/bin/env bash
# List all permissions in the Meta App Review submission and their status.
# Usage: bash list-permissions.sh [app_id] [access_token]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

APP_ID="${1:-1936126137016578}"
TOKEN="${2:-}"

if [ -z "$TOKEN" ]; then
  TOKEN="$(grep -E '^FACEBOOK_ACCESS_TOKEN=' "$REPO_ROOT/.env" 2>/dev/null | cut -d'=' -f2- || true)"
fi

if [ -z "$TOKEN" ]; then
  echo "Error: No access token provided."
  echo "Usage: list-permissions.sh [app_id] [access_token]"
  exit 1
fi

echo "Listing permissions for app $APP_ID..."
echo ""

RESP=$(curl -s "https://graph.facebook.com/v21.0/${APP_ID}/permissions?access_token=${TOKEN}")
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"

echo ""
echo "=== Permission status reference ==="
echo ""
echo "Completed (allowed-usage saved):"
for p in pages_show_list pages_manage_metadata pages_messaging business_management \
         pages_read_engagement instagram_business_basic instagram_business_manage_messages \
         pages_read_user_content pages_manage_posts pages_manage_engagement \
         pages_utility_messaging; do
  echo "  [x] $p"
done

echo ""
echo "Remaining (allowed-usage not yet saved):"
for p in instagram_business_content_publish instagram_manage_comments \
         instagram_business_manage_insights threads_basic read_insights; do
  echo "  [ ] $p"
done
