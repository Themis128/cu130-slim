#!/usr/bin/env bash
# Save Twitter/X v1.1 API credentials.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API_KEY="${1:-}"
API_SECRET="${2:-}"
ACCESS_TOKEN="${3:-}"
ACCESS_TOKEN_SECRET="${4:-}"
if [ -z "$API_KEY" ] || [ -z "$API_SECRET" ] || [ -z "$ACCESS_TOKEN" ] || [ -z "$ACCESS_TOKEN_SECRET" ]; then
  echo "Usage: $(basename "$0") <api-key> <api-secret> <access-token> <access-token-secret>"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/set-secret.sh" TWITTER_API_KEY "$API_KEY" "Twitter/X API key"
"$SCRIPT_DIR/set-secret.sh" TWITTER_API_SECRET "$API_SECRET" "Twitter/X API secret"
"$SCRIPT_DIR/set-secret.sh" TWITTER_ACCESS_TOKEN "$ACCESS_TOKEN" "Twitter/X access token"
"$SCRIPT_DIR/set-secret.sh" TWITTER_ACCESS_TOKEN_SECRET "$ACCESS_TOKEN_SECRET" "Twitter/X access token secret"
