#!/bin/bash
# tailnet-admin-plane.sh — expose authentik /if/admin over tailscale serve on omv.
HOST=${HOST:-tbaltzakis@100.74.191.58}; PASS=${OMV_SUDO_PASS:?set OMV_SUDO_PASS}
ssh -o BatchMode=yes $HOST "echo $PASS | sudo -S sh -c '
  tailscale serve status 2>/dev/null | head -3
  tailscale serve --bg --set-path admin http://127.0.0.1:30910/if/admin 2>&1 | tail -3
  tailscale serve status | head -8'"
echo "admin URL: https://$(hostname).$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin).get(\"MagicDNSSuffix\",\"\"))' 2>/dev/null || echo omv.TAILNET.ts.net)/admin"
