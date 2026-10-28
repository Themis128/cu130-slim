#!/bin/sh
# Start cloudflared named tunnel if SOCIAL_TUNNEL_TOKEN is set,
# otherwise fall back to quick tunnel.
mkdir -p /run/tunnel

if [ -n "$SOCIAL_TUNNEL_TOKEN" ]; then
  printf '[cloudflared] Starting named tunnel for social.cloudless.gr\n' >&2
  printf 'https://social.cloudless.gr' > /run/tunnel/url
  # http2 (TCP) instead of default QUIC: WSL2 NAT drops idle UDP flow state,
  # which killed all QUIC edge connections for ~3min (Cloudflare 1033).
  exec cloudflared tunnel --no-autoupdate run --protocol http2 --token "$SOCIAL_TUNNEL_TOKEN"
else
  printf '[cloudflared] Starting quick tunnel (no SOCIAL_TUNNEL_TOKEN set)\n' >&2
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
fi
