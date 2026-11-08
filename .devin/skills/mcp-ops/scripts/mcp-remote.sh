#!/bin/bash
# Generic mcp-remote launcher — resolves a working node + proxy.js across
# WSL (Linux node + ~/.local/lib) and Windows Git Bash (node.exe + /mnt/c lib).
# Usage: mcp-remote.sh <remote-url> [mcp-remote args...]
set -euo pipefail

_LIB=""
for _d in "$HOME/.local/lib" /mnt/c/Users/tbaltzakis/.local/lib /c/Users/tbaltzakis/.local/lib; do
  [[ -f "$_d/mcp-remote/node_modules/mcp-remote/dist/proxy.js" ]] && _LIB=$_d && break
done
: "${_LIB:?mcp-remote package not found under ~/.local/lib or /mnt/c/Users/tbaltzakis/.local/lib}"

_MOD="$_LIB/mcp-remote/node_modules/mcp-remote/dist/proxy.js"

if [[ -x /home/tbaltzakis/.local/bin/node ]]; then
  _NODE=/home/tbaltzakis/.local/bin/node
elif command -v node >/dev/null 2>&1 && [[ "$(command -v node)" != /mnt/* ]]; then
  _NODE=node
elif [[ -x "/mnt/c/Program Files/nodejs/node.exe" ]]; then
  _NODE="/mnt/c/Program Files/nodejs/node.exe"
else
  _NODE="/c/Program Files/nodejs/node.exe"
fi

# Windows node binaries need a Windows-style module path.
if [[ "$_NODE" == *.exe || "$_NODE" == /mnt/* || "$_NODE" == /c/* ]]; then
  command -v wslpath >/dev/null 2>&1 && _MOD="$(wslpath -w "$_MOD")"
fi

exec "$_NODE" "$_MOD" "$@"
