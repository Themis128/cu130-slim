#!/usr/bin/env bash
# Check env vars for all OAuth platforms.
# Usage: bash check-oauth-env.sh
set -euo pipefail

cd "$(dirname "$0")/../../.."

set -a
source .env 2>/dev/null || true
set +a

echo "=== OAuth Environment Variables ==="
echo ""

check_var() {
    local name="$1"
    local val="${!name:-}"
    if [ -z "$val" ]; then
        echo "  MISSING: $name"
    else
        echo "  OK: $name (${#val} chars)"
    fi
}

check_redirect() {
    local name="$1"
    local val="${!name:-}"
    if [ -z "$val" ]; then
        echo "  MISSING: $name"
    elif echo "$val" | grep -q "^https://"; then
        echo "  OK: $name ($val)"
    elif echo "$val" | grep -q "^http://localhost"; then
        echo "  WARNING: $name uses localhost ($val) — Meta/Twitter may reject in production"
    else
        echo "  WARNING: $name has unexpected format ($val)"
    fi
}

echo "--- LinkedIn ---"
check_var LINKEDIN_CLIENT_ID
check_var LINKEDIN_CLIENT_SECRET
check_redirect LINKEDIN_REDIRECT_URI

echo ""
echo "--- Twitter/X ---"
check_var TWITTER_CLIENT_ID
check_var TWITTER_CLIENT_SECRET
check_redirect TWITTER_REDIRECT_URI

echo ""
echo "--- Facebook ---"
check_var FACEBOOK_CLIENT_ID
check_var FACEBOOK_CLIENT_SECRET
check_redirect FACEBOOK_REDIRECT_URI

echo ""
echo "--- Instagram ---"
check_var INSTAGRAM_CLIENT_ID
check_var INSTAGRAM_CLIENT_SECRET
check_redirect INSTAGRAM_REDIRECT_URI

echo ""
echo "--- Threads ---"
check_var THREADS_CLIENT_ID
check_var THREADS_CLIENT_SECRET
check_redirect THREADS_REDIRECT_URI

echo ""
echo "--- TikTok ---"
check_var TIKTOK_CLIENT_KEY
check_var TIKTOK_CLIENT_SECRET
check_redirect TIKTOK_REDIRECT_URI
