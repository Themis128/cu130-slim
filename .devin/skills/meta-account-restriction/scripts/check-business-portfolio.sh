#!/usr/bin/env bash
# Check Meta business portfolio health and verification status.
# Usage: bash check-business-portfolio.sh [business_id] [access_token]
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
  echo "Usage: check-business-portfolio.sh [business_id] [access_token]"
  exit 1
fi

echo "Checking business portfolio $BIZ_ID..."
echo ""

RESP=$(curl -s "https://graph.facebook.com/v21.0/${BIZ_ID}?access_token=${TOKEN}&fields=name,verification_status,vertical,legal_name,business_address,primary_page,two_factor_required")
echo "Business portfolio:"
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
echo ""

echo "=== Web-based checks ==="
echo "Business portfolio detail: https://www.facebook.com/business-support-home/${BIZ_ID}/"
echo "Business verification: https://developers.facebook.com/apps/1936126137016578/app-review/verification"
echo ""
echo "If verification is blocked, the personal account admin has restrictions."
echo "See: meta-account-restriction skill for resolution steps."
