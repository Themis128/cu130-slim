#!/usr/bin/env bash
# mcp_wsl_share.sh — share the Windows Docker MCP Toolkit state with WSL.
# Idempotent: detects a real dir (backup + symlink), an existing symlink
# (verify), or missing state (fresh symlink). Run inside Ubuntu-24.04.
set -u

DOCKER_DIR="$HOME/.docker"
MCP="$DOCKER_DIR/mcp"
WIN_MCP="/mnt/c/Users/tbaltzakis/.docker/mcp"
STAMP="$(date +%Y%m%d)"

if [ ! -d "$WIN_MCP" ]; then
  echo "FAIL: Windows MCP dir not reachable at $WIN_MCP (is /mnt/c mounted?)" >&2
  exit 1
fi

if [ -L "$MCP" ]; then
  tgt="$(readlink "$MCP")"
  if [ "$tgt" = "$WIN_MCP" ]; then
    echo "OK: mcp already symlinked -> $tgt"
  else
    echo "RELINK: mcp -> $tgt, re-pointing to $WIN_MCP"
    mv "$MCP" "$MCP.old-$STAMP" && ln -s "$WIN_MCP" "$MCP"
  fi
elif [ -d "$MCP" ]; then
  echo "BACKUP+LINK: existing mcp dir -> $MCP.bak-$STAMP"
  mv "$MCP" "$MCP.bak-$STAMP" && ln -s "$WIN_MCP" "$MCP"
else
  ln -s "$WIN_MCP" "$MCP" && echo "LINK: created fresh symlink"
fi

ls -la "$DOCKER_DIR" | grep -E ' mcp($| )' || true
echo "--- verify ---"
docker mcp profile ls 2>&1 | head -8
