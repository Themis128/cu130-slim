#!/bin/bash
# sudo-env.sh — OMV_SUDO_PASS plumbing: resolves the password from the secret
# store (never hardcode). Sources in order: $OMV_SUDO_PASS, .env OMV_SUDO_PASS,
# secret-store file ~/.config/secrets/omv-sudo (chmod 600).
[ -n "$OMV_SUDO_PASS" ] && { export OMV_SUDO_PASS; return 0 2>/dev/null || exit 0; }
for f in /home/tbaltzakis/cu130-slim/.env "$HOME/.config/secrets/omv-sudo"; do
  [ -f "$f" ] && V=$(grep -m1 -E '^(OMV_SUDO_PASS=|.*$)' "$f" | cut -d= -f2 | tr -d '"'"' \r') && [ -n "$V" ] && { export OMV_SUDO_PASS=$V; echo "loaded from $f"; exit 0; }
done
echo "OMV_SUDO_PASS not found — create ~/.config/secrets/omv-sudo (chmod 600) or add to .env"; exit 2
