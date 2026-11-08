#!/bin/bash
# Launcher for Nuraveda linkedin-ads-mcp (uvx runtime).
# Supplies LINKEDIN_ACCESS_TOKEN by decrypting the linkedin social_account
# token inside social-api (same selection as app.services.linkedin_ads._token).
# Env override: set LINKEDIN_ACCESS_TOKEN to skip the docker lookup.
set -euo pipefail

if [[ -z "${LINKEDIN_ACCESS_TOKEN:-}" ]]; then
  _ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)/.env"
  if [[ -f "$_ENV_FILE" ]]; then
    LINKEDIN_ACCESS_TOKEN="$(sed -n 's/^LINKEDIN_ACCESS_TOKEN=//p' "$_ENV_FILE" | head -1 | tr -d "\"'")"
  fi
fi
if [[ -z "${LINKEDIN_ACCESS_TOKEN:-}" ]]; then
  LINKEDIN_ACCESS_TOKEN="$(docker exec -w /app -e PYTHONPATH=/app social-api \
    python3 /app/scripts/linkedin_token_dump.py 2>/dev/null | tail -1)"
fi
: "${LINKEDIN_ACCESS_TOKEN:?could not resolve a LinkedIn access token}"
export LINKEDIN_ACCESS_TOKEN LINKEDIN_API_VERSION="${LINKEDIN_API_VERSION:-202511}"

UVX="$(command -v uvx || echo "$HOME/.local/bin/uvx")"
exec "$UVX" linkedin-ads-mcp "$@"
