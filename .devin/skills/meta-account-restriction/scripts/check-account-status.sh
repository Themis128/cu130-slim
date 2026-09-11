#!/usr/bin/env bash
# Check personal Facebook account status via Graph API.
# Usage: bash check-account-status.sh [user_id] [access_token]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

USER_ID="${1:-me}"
TOKEN="${2:-}"

if [ -z "$TOKEN" ]; then
  TOKEN="$(grep -E '^FACEBOOK_ACCESS_TOKEN=' "$REPO_ROOT/.env" 2>/dev/null | cut -d'=' -f2- || true)"
fi

if [ -z "$TOKEN" ]; then
  echo "Error: No access token provided."
  echo "Usage: check-account-status.sh [user_id] [access_token]"
  exit 1
fi

echo "Checking Facebook account status..."
echo ""

RESP=$(curl -s "https://graph.facebook.com/v21.0/${USER_ID}?access_token=${TOKEN}&fields=name,id,account_status,can_advertise,can_use_messenger")
echo "Account info:"
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
echo ""

echo "=== Web-based checks ==="
echo "Account Status page: https://www.facebook.com/account_status"
echo "Account Quality page: https://www.facebook.com/accountquality"
echo "Business Support Home: https://www.facebook.com/business-support-home/"
