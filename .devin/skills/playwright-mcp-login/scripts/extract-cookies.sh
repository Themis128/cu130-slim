#!/usr/bin/env bash
# Extract cookies from the Playwright MCP browser session.
#
# Usage:
#   extract-cookies.sh <platform>
#
# Platforms: twitter, tiktok, threads, instagram
#
# Outputs cookie JSON to stdout. The agent should call browser_evaluate
# via the Playwright MCP server to get document.cookie, then this script
# formats it. For HTTP-only cookies, the browser-novnc bridge /session/cookies
# endpoint is used after navigation.
set -euo pipefail

PLATFORM="${1:-}"
if [[ -z "$PLATFORM" ]]; then
  echo "Usage: $0 <platform>" >&2
  echo "Platforms: twitter, tiktok, threads, instagram" >&2
  exit 1
fi

case "$PLATFORM" in
  twitter|x)
    DOMAIN=".x.com"
    ;;
  tiktok)
    DOMAIN=".tiktok.com"
    ;;
  threads)
    DOMAIN=".threads.com"
    ;;
  instagram)
    DOMAIN=".instagram.com"
    ;;
  *)
    echo "Unknown platform: $PLATFORM" >&2
    exit 1
    ;;
esac

echo "=== Cookie extraction for $PLATFORM ===" >&2
echo "Domain: $DOMAIN" >&2
echo "" >&2
echo "The agent should use the Playwright MCP browser_evaluate tool to run:" >&2
echo "  () => document.cookie" >&2
echo "" >&2
echo "Then parse the cookie string and output as JSON." >&2
echo "" >&2
echo "For HTTP-only cookies, navigate the browser-novnc bridge to the platform" >&2
echo "and use GET /session/cookies to extract the full cookie jar." >&2
echo "" >&2
echo "Example output format:" >&2
cat <<'JSON'
[
  {"name": "auth_token", "value": "...", "domain": ".x.com", "path": "/", "secure": true, "httpOnly": true},
  {"name": "ct0", "value": "...", "domain": ".x.com", "path": "/", "secure": true, "httpOnly": false}
]
JSON
