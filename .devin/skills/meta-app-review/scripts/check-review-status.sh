#!/usr/bin/env bash
# Check Meta App Review submission status via Graph API.
# Usage: bash check-review-status.sh [app_id] [access_token]
# If no access token is provided, reads from .env FACEBOOK_ACCESS_TOKEN.
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
  echo "Usage: check-review-status.sh [app_id] [access_token]"
  echo "Or set FACEBOOK_ACCESS_TOKEN in .env"
  exit 1
fi

echo "Checking App Review status for app $APP_ID..."
echo ""

# Get app review status
RESP=$(curl -s "https://graph.facebook.com/v21.0/${APP_ID}/app_review_status?access_token=${TOKEN}")
echo "Review status:"
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
echo ""

# Get submission details
SUBMISSION_ID="2047300442565813"
RESP2=$(curl -s "https://graph.facebook.com/v21.0/${SUBMISSION_ID}?access_token=${TOKEN}&fields=status,submitted_time,permissions")
echo "Submission $SUBMISSION_ID:"
echo "$RESP2" | python3 -m json.tool 2>/dev/null || echo "$RESP2"
