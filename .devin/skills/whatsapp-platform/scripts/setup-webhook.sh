#!/usr/bin/env bash
# Configure the WhatsApp webhook URL in the Meta App Dashboard via Graph API.
# Usage: bash setup-webhook.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Load env
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  source "$REPO_ROOT/.env"
  set +a
fi

APP_ID="${META_APP_ID:-1936126137016578}"
APP_SECRET="${FACEBOOK_APP_SECRET:-}"
ACCESS_TOKEN="${WHATSAPP_ACCESS_TOKEN:-${FACEBOOK_ACCESS_TOKEN:-}}"
CALLBACK_URL="${WHATSAPP_CALLBACK_URL:-https://social.cloudless.gr/api/v1/whatsapp/webhook}"
VERIFY_TOKEN="${WHATSAPP_VERIFY_TOKEN:-cloudless_whatsapp_verify}"

if [ -z "$APP_SECRET" ] || [ -z "$ACCESS_TOKEN" ]; then
  echo "Error: FACEBOOK_APP_SECRET and WHATSAPP_ACCESS_TOKEN must be set"
  echo "Set them in $REPO_ROOT/.env or as environment variables"
  exit 1
fi

echo "=== WhatsApp Webhook Setup ==="
echo "App ID: $APP_ID"
echo "Callback URL: $CALLBACK_URL"
echo "Verify Token: $VERIFY_TOKEN"
echo ""

# Register the webhook subscription
echo "1. Registering webhook subscription..."
RESULT=$(curl -s -X POST \
  "https://graph.facebook.com/v21.0/$APP_ID/subscriptions" \
  -H "Content-Type: application/json" \
  -d "$(cat <<EOF
{
  "object": "whatsapp_business_account",
  "callback_url": "$CALLBACK_URL",
  "verify_token": "$VERIFY_TOKEN",
  "fields": ["messages"]
}
EOF
)" 2>&1 || true)

echo "   Result: $RESULT"
echo ""

# Note: This requires the app access token with whatsapp_business_management permission.
# In practice, this is often done manually in the Meta App Dashboard:
# https://developers.facebook.com/apps/1936126137016578/whatsapp/
echo "2. If the API call failed, configure manually:"
echo "   a. Go to: https://developers.facebook.com/apps/$APP_ID/whatsapp/"
echo "   b. Navigate to: WhatsApp > Configuration"
echo "   c. Set Callback URL to: $CALLBACK_URL"
echo "   d. Set Verify Token to: $VERIFY_TOKEN"
echo "   e. Click 'Verify and Save'"
echo "   f. Subscribe to the 'messages' field"
echo ""
echo "3. Test the webhook:"
echo "   bash $SCRIPT_DIR/check-webhook.sh"
