#!/bin/bash
# apply.sh — apply the authentik target architecture end-to-end (idempotent).
set -e
HOST=tbaltzakis@100.95.117.84; ENV=/home/tbaltzakis/cu130-slim/.env
REPO=/home/tbaltzakis/omv-infrastructure
echo "[1/5] k3s health"; ssh -o BatchMode=yes $HOST 'sudo -n systemctl is-active k3s' || echo "WARN: k3s check needs sudo password (OMV_SUDO_PASS)"
echo "[2/5] token"; grep -q '^AUTHENTIK_ADMIN_TOKEN=.' $ENV 2>/dev/null || bash "$(dirname "$0")/mint-admin-token.sh" $ENV
echo "[3/5] app meta"; python3 /home/tbaltzakis/cu130-slim/.devin/skills/authentik-ops/scripts/update_app_meta.py --env $ENV --app social_client --note "Host env: WSL2 Ubuntu-24.04, Ubuntu Pro ESM, repos /home/tbaltzakis, tailnet admin plane pending" || true
echo "[4/5] blueprint"; TOK=$(grep '^AUTHENTIK_ADMIN_TOKEN=' $ENV | cut -d= -f2); [ -n "$TOK" ] && python3 - <<PY
import json,urllib.request
bp=open("$REPO/omv/authentik/blueprint.yaml").read()
r=urllib.request.Request("https://auth.cloudless.gr/api/v3/managed/blueprints/",method="POST",data=json.dumps({"name":"cloudless-platform","path":"omv/authentik/blueprint.yaml","context":{},"metadata":{"name":"cloudless-platform"},"content":bp}).encode(),headers={"Authorization":"Bearer $TOK","Content-Type":"application/json"})
try:
    print(json.load(urllib.request.urlopen(r,timeout=20)))
except Exception as e: print("blueprint apply:",e)
PY
echo "[5/5] manual gates remaining:"; echo " - CF Access policy /if/admin -> tailnet (dash.zero-trust)"
echo " - tailnet ACL tag:authentik-admin (login.tailscale.com/admin/acls)"
echo " - tailscale serve on omv-ha: sudo tailscale serve --bg --set-path admin http://192.168.1.128:30910/if/admin"
