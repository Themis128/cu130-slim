#!/bin/sh
# Start cloudflared quick tunnel, write URL to shared volume file when ready.
mkdir -p /run/tunnel

cloudflared tunnel --no-autoupdate --url http://social-api:8000 2>&1 | \
  while IFS= read -r line; do
    printf '%s\n' "$line"
    case "$line" in
      *trycloudflare.com*)
        url=$(printf '%s' "$line" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com')
        if [ -n "$url" ]; then
          printf '%s' "$url" > /run/tunnel/url
          printf '[cloudflared] Tunnel URL saved: %s\n' "$url" >&2
        fi
        ;;
    esac
  done
