#!/usr/bin/env bash
# Verify Meta OAuth authorize URLs for Facebook, Instagram, and Threads.
# Usage: bash verify-oauth-urls.sh
set -euo pipefail

cd "$(dirname "$0")/../../.."

# Load env vars
set -a
source .env 2>/dev/null || true
set +a

echo "=== Meta OAuth URL Verification ==="
echo ""

# Facebook
FB_REDIRECT="${FACEBOOK_REDIRECT_URI:-https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback}"
FB_URL="https://www.facebook.com/dialog/oauth?client_id=${FACEBOOK_CLIENT_ID:-MISSING}&redirect_uri=${FB_REDIRECT}&scope=public_profile,email,pages_show_list,pages_read_engagement,pages_manage_posts&response_type=code&state=test"
echo "Facebook authorize URL:"
echo "  ${FB_URL}"
echo "  client_id: ${FACEBOOK_CLIENT_ID:-MISSING}"
echo "  redirect_uri: ${FB_REDIRECT}"
echo ""

# Instagram (FB Login flow)
IG_REDIRECT="${INSTAGRAM_REDIRECT_URI:-https://social.cloudless.gr/api/v1/auth/oauth/instagram/callback}"
IG_URL="https://www.facebook.com/dialog/oauth?client_id=${INSTAGRAM_CLIENT_ID:-MISSING}&redirect_uri=${IG_REDIRECT}&scope=instagram_basic,instagram_content_publish,pages_show_list&response_type=code&state=test"
echo "Instagram authorize URL (FB Login flow):"
echo "  ${IG_URL}"
echo "  client_id: ${INSTAGRAM_CLIENT_ID:-MISSING}"
echo "  redirect_uri: ${IG_REDIRECT}"
echo ""

# Threads
TH_REDIRECT="${THREADS_REDIRECT_URI:-https://social.cloudless.gr/api/v1/auth/oauth/threads/callback}"
TH_URL="https://threads.net/oauth/authorize?client_id=${THREADS_CLIENT_ID:-MISSING}&redirect_uri=${TH_REDIRECT}&scope=threads_basic,threads_content_publish&response_type=code&state=test"
echo "Threads authorize URL:"
echo "  ${TH_URL}"
echo "  client_id: ${THREADS_CLIENT_ID:-MISSING}"
echo "  redirect_uri: ${TH_REDIRECT}"
echo ""

echo "=== Checks ==="
[ -z "${FACEBOOK_CLIENT_ID:-}" ] && echo "WARNING: FACEBOOK_CLIENT_ID is empty" || echo "OK: FACEBOOK_CLIENT_ID set"
[ -z "${FACEBOOK_CLIENT_SECRET:-}" ] && echo "WARNING: FACEBOOK_CLIENT_SECRET is empty" || echo "OK: FACEBOOK_CLIENT_SECRET set"
[ -z "${INSTAGRAM_CLIENT_ID:-}" ] && echo "WARNING: INSTAGRAM_CLIENT_ID is empty" || echo "OK: INSTAGRAM_CLIENT_ID set"
[ -z "${INSTAGRAM_CLIENT_SECRET:-}" ] && echo "WARNING: INSTAGRAM_CLIENT_SECRET is empty" || echo "OK: INSTAGRAM_CLIENT_SECRET set"
[ -z "${THREADS_CLIENT_ID:-}" ] && echo "WARNING: THREADS_CLIENT_ID is empty" || echo "OK: THREADS_CLIENT_ID set"
[ -z "${THREADS_CLIENT_SECRET:-}" ] && echo "WARNING: THREADS_CLIENT_SECRET is empty" || echo "OK: THREADS_CLIENT_SECRET set"

echo ""
echo "NOTE: Open each URL in a browser to verify the OAuth consent screen appears."
echo "      Meta requires HTTPS redirect URIs in production mode."
