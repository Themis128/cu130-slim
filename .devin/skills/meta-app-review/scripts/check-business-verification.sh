#!/usr/bin/env bash
# Check Meta business verification status.
# Usage: bash check-business-verification.sh [business_id] [access_token]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

BIZ_ID="${1:-1558125105019725}"
TOKEN="${2:-}"

if [ -z "$TOKEN" ]; then
  TOKEN="$(grep -E '^FACEBOOK_ACCESS_TOKEN=' "$REPO_ROOT/.env" 2>/dev/null | cut -d'=' -f2- || true)"
fi

if [ -z "$TOKEN" ]; then
  echo "Error: No access token provided."
  echo "Usage: check-business-verification.sh [business_id] [access_token]"
  exit 1
fi

echo "Checking business verification for portfolio $BIZ_ID..."
echo ""

RESP=$(curl -s "https://graph.facebook.com/v21.0/${BIZ_ID}?access_token=${TOKEN}&fields=name,verification_status,vertical,legal_name,business_address")
echo "Business portfolio:"
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
echo ""

# Check if the business can start verification
echo "Verification URL: https://developers.facebook.com/apps/1936126137016578/app-review/verification"
echo ""
echo "If verification is blocked, check:"
echo "  1. Personal account status: https://www.facebook.com/account_status"
echo "  2. Account Quality: https://www.facebook.com/accountquality"
echo "  3. Business Support Home: https://www.facebook.com/business-support-home/"
