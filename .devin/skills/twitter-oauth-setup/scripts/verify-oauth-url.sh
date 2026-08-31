#!/usr/bin/env bash
# Verify Twitter/X OAuth authorize URL.
# Usage: bash verify-oauth-url.sh
set -euo pipefail

cd "$(dirname "$0")/../../.."

set -a
source .env 2>/dev/null || true
set +a

echo "=== Twitter/X OAuth URL Verification ==="
echo ""

TW_REDIRECT="${TWITTER_REDIRECT_URI:-https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback}"

# Generate a PKCE pair for testing
CODE_VERIFIER=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
CODE_CHALLENGE=$(python3 -c "
import hashlib, base64, sys
v = sys.argv[1]
c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b'=').decode()
print(c)
" "$CODE_VERIFIER")

TW_URL="https://twitter.com/i/oauth2/authorize?client_id=${TWITTER_CLIENT_ID:-MISSING}&redirect_uri=${TW_REDIRECT}&response_type=code&scope=tweet.read tweet.write users.read offline.access&code_challenge=${CODE_CHALLENGE}&code_challenge_method=S256&state=test"

echo "Twitter authorize URL:"
echo "  ${TW_URL}"
echo "  client_id: ${TWITTER_CLIENT_ID:-MISSING}"
echo "  redirect_uri: ${TW_REDIRECT}"
echo "  code_challenge: ${CODE_CHALLENGE:0:20}..."
echo "  code_verifier: ${CODE_VERIFIER:0:20}..."
echo ""

echo "=== Checks ==="
[ -z "${TWITTER_CLIENT_ID:-}" ] && echo "WARNING: TWITTER_CLIENT_ID is empty" || echo "OK: TWITTER_CLIENT_ID set"
[ -z "${TWITTER_CLIENT_SECRET:-}" ] && echo "WARNING: TWITTER_CLIENT_SECRET is empty" || echo "OK: TWITTER_CLIENT_SECRET set"
[ -z "${TWITTER_REDIRECT_URI:-}" ] && echo "WARNING: TWITTER_REDIRECT_URI is empty" || echo "OK: TWITTER_REDIRECT_URI set"

echo ""
echo "NOTE: Open the URL in a browser to verify the OAuth consent screen appears."
echo "      Twitter requires HTTPS redirect URIs."
