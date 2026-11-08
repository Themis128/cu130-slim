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
    [[ -d $_d/metabase-mcp ]] && _LIB=$_d && break
  done
fi

_MOD="$_LIB/metabase-mcp/node_modules/@cognitionai/metabase-mcp-server/dist/server.js"
# Only translate to a Windows path when the node binary is a Windows one —
# wslpath output breaks Linux node even under WSL.
if [[ "$_NODE" == *.exe || "$_NODE" == /mnt/* || "$_NODE" == /c/* ]]; then
  command -v wslpath >/dev/null 2>&1 && _MOD="$(wslpath -w "$_MOD")"
fi
exec "$_NODE" "$_MOD" "$@"
