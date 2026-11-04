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

# Dual-OS launcher - WSL paths when present, else Windows Git Bash.
if [[ -x /home/tbaltzakis/.local/bin/node ]]; then
  _NODE=/home/tbaltzakis/.local/bin/node
  _LIB=/home/tbaltzakis/.local/lib
else
  if command -v node >/dev/null 2>&1; then
    _NODE=node
  elif [[ -x "/mnt/c/Program Files/nodejs/node.exe" ]]; then
    _NODE="/mnt/c/Program Files/nodejs/node.exe"
  else
    _NODE="/c/Program Files/nodejs/node.exe"
  fi
  _LIB=""
  for _d in "$HOME/.local/lib" /mnt/c/Users/tbaltzakis/.local/lib /c/Users/tbaltzakis/.local/lib; do
    [[ -d $_d/mcp-remote ]] && _LIB=$_d && break
  done
fi

_MOD="$_LIB/mcp-remote/node_modules/mcp-remote/dist/proxy.js"
command -v wslpath >/dev/null 2>&1 && _MOD="$(wslpath -w "$_MOD")"
exec "$_NODE" "$_MOD" \
  https://mcp.polar.sh/mcp/polar-mcp \
  --header "Authorization: Bearer \${POLAR_ACCESS_TOKEN}" \
  "$@"
