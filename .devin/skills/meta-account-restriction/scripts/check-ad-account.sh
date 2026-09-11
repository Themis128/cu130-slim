#!/usr/bin/env bash
# Check Meta ad account restriction status via Graph API.
# Usage: bash check-ad-account.sh [ad_account_id] [access_token]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

AD_ACCOUNT_ID="${1:-657781691826702}"
TOKEN="${2:-}"

if [ -z "$TOKEN" ]; then
  TOKEN="$(grep -E '^FACEBOOK_ACCESS_TOKEN=' "$REPO_ROOT/.env" 2>/dev/null | cut -d'=' -f2- || true)"
fi

if [ -z "$TOKEN" ]; then
  echo "Error: No access token provided."
  echo "Usage: check-ad-account.sh [ad_account_id] [access_token]"
  exit 1
fi

echo "Checking ad account $AD_ACCOUNT_ID..."
echo ""

RESP=$(curl -s "https://graph.facebook.com/v21.0/act_${AD_ACCOUNT_ID}?access_token=${TOKEN}&fields=account_status,name,amount_spent,balance,currency,disable_reason,ad_account_detailed_status")
echo "Ad account info:"
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
echo ""

# Account status codes:
# 1 = ACTIVE
# 2 = DISABLED
# 3 = UNSETTLED
# 7 = PENDING_RISK_REVIEW
# 8 = PENDING_SETTLEMENT
# 9 = IN_GRACE_PERIOD
# 100 = PENDING_REVIEW
# 101 = TEMPORARILY_UNAVAILABLE

echo "=== Status reference ==="
echo "1=ACTIVE, 2=DISABLED, 3=UNSETTLED, 7=PENDING_RISK_REVIEW"
echo "8=PENDING_SETTLEMENT, 9=IN_GRACE_PERIOD, 100=PENDING_REVIEW"
echo ""
echo "=== Web-based check ==="
echo "Ad account detail: https://www.facebook.com/business-support-home/1134463867/${AD_ACCOUNT_ID}/"
