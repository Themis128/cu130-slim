#!/bin/bash
# Launcher for @thenavidm/google-photos-mcp-cli.
# Pulls GOOGLE_PHOTOS_* credentials from the repo .env (gitignored) so no
# secret is committed to mcp_config.json.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

_get() {
  local key="$1"
  if [[ -f "$ENV_FILE" ]]; then
    sed -n "s/^${key}=//p" "$ENV_FILE" | head -1 | tr -d "\"'"
  fi
}

export GOOGLE_PHOTOS_CLIENT_ID="${GOOGLE_PHOTOS_CLIENT_ID:-$(_get GOOGLE_PHOTOS_CLIENT_ID)}"
export GOOGLE_PHOTOS_CLIENT_SECRET="${GOOGLE_PHOTOS_CLIENT_SECRET:-$(_get GOOGLE_PHOTOS_CLIENT_SECRET)}"
export GOOGLE_PHOTOS_REFRESH_TOKEN="${GOOGLE_PHOTOS_REFRESH_TOKEN:-$(_get GOOGLE_PHOTOS_REFRESH_TOKEN)}"

: "${GOOGLE_PHOTOS_CLIENT_ID:?GOOGLE_PHOTOS_CLIENT_ID not set — see google-photos-ops SKILL.md}"
: "${GOOGLE_PHOTOS_CLIENT_SECRET:?GOOGLE_PHOTOS_CLIENT_SECRET not set}"
: "${GOOGLE_PHOTOS_REFRESH_TOKEN:?GOOGLE_PHOTOS_REFRESH_TOKEN not set — run scripts/login.sh once}"

exec npx -y @thenavidm/google-photos-mcp-cli@2.0.1 "$@"
