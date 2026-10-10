#!/bin/bash
# Wrapper for Polar's hosted MCP server (https://mcp.polar.sh/mcp/polar-mcp).
# Reads POLAR_ACCESS_TOKEN from the repo .env so no secret is committed.
# mcp-remote expands ${POLAR_ACCESS_TOKEN} inside --header itself, keeping the
# token out of the process argv.
set -euo pipefail

# Extract only POLAR_ACCESS_TOKEN — the .env has lines that are not valid bash
# (unquoted values), so sourcing the whole file aborts under set -euo pipefail.
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)/.env"
if [[ -z "${POLAR_ACCESS_TOKEN:-}" && -f "$ENV_FILE" ]]; then
  POLAR_ACCESS_TOKEN="$(sed -n 's/^POLAR_ACCESS_TOKEN=//p' "$ENV_FILE" | head -1 | tr -d "\"'")"
fi
export POLAR_ACCESS_TOKEN

: "${POLAR_ACCESS_TOKEN:?POLAR_ACCESS_TOKEN not set — add it to the repo .env (see polar-ops SKILL.md)}"

exec /home/tbaltzakis/.local/bin/node \
  /home/tbaltzakis/.local/lib/mcp-remote/node_modules/mcp-remote/dist/proxy.js \
  https://mcp.polar.sh/mcp/polar-mcp \
  --header "Authorization: Bearer \${POLAR_ACCESS_TOKEN}" \
  "$@"
