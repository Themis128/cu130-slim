#!/usr/bin/env bash
# Check TikTok app configuration from .env and verify the app is reachable.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

echo "=== TikTok App Configuration ==="

# Read env vars without exposing secrets
source .env 2>/dev/null

echo "Client key:        ${TIKTOK_CLIENT_KEY:-<not set>}"
echo "Client secret:     $([ -n "${TIKTOK_CLIENT_SECRET:-}" ] && echo '<set>' || echo '<not set>')"
echo "Redirect URI:      ${TIKTOK_REDIRECT_URI:-<not set>}"
echo ""
echo "App ID:            7630494700880906241"
echo "App name:          Cloudless"
echo "Ownership:         Individual (needs transfer to organization)"
echo "Target org:        cloudless.gr (7630331010873377809)"
echo "Mode:              Sandbox (unaudited)"
echo ""

echo "=== Connected TikTok Account ==="
.devin/skills/socialauto-accounts/scripts/list-accounts.sh 2>/dev/null | grep tiktok || echo "No TikTok account connected"
echo ""

echo "=== Redirect URI Reachability ==="
if [ -n "${TIKTOK_REDIRECT_URI:-}" ]; then
  # Check if the callback endpoint is reachable
  CALLBACK_URL="${TIKTOK_REDIRECT_URI}"
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$CALLBACK_URL" 2>/dev/null || echo "000")
  echo "Callback endpoint: $HTTP_CODE (expect 405 or 400 — endpoint exists but needs auth params)"
else
  echo "No TIKTOK_REDIRECT_URI set"
fi
echo ""

echo "=== Media URL Reachability (for PULL_FROM_URL) ==="
TUNNEL_URL=$(docker compose exec -T social-api cat /run/tunnel/url 2>/dev/null || echo "")
if [ -n "$TUNNEL_URL" ]; then
  echo "Tunnel URL: $TUNNEL_URL"
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$TUNNEL_URL/health" 2>/dev/null || echo "000")
  echo "Health endpoint: $HTTP_CODE"
  echo ""
  echo "Domain verification status: check in TikTok developer console"
  echo "  → https://developers.tiktok.com → Cloudless app → URL properties"
else
  echo "No tunnel URL found — Cloudflare tunnel may not be running"
fi
