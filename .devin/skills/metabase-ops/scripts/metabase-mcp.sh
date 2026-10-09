#!/bin/bash
# Wrapper for @cognitionai/metabase-mcp-server.
# Sources the untracked .devin/metabase-mcp.env (METABASE_URL + METABASE_API_KEY)
# so no secret is committed to the repo.
set -euo pipefail

ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)/.devin/metabase-mcp.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

: "${METABASE_URL:?METABASE_URL not set — create .devin/metabase-mcp.env (see metabase-ops SKILL.md)}"
: "${METABASE_API_KEY:?METABASE_API_KEY not set — create .devin/metabase-mcp.env}"

exec /home/tbaltzakis/.local/bin/node \
  /home/tbaltzakis/.local/lib/metabase-mcp/node_modules/@cognitionai/metabase-mcp-server/dist/server.js \
  "$@"
