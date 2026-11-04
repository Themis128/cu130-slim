#!/bin/bash
# mint-admin-token.sh [env-file] — mint authentik admin token on omv-ha k3s, write to .env.
ENV=${1:-/home/tbaltzakis/cu130-slim/.env}
HOST=tbaltzakis@100.95.117.84
POD=$(ssh -o BatchMode=yes $HOST 'sudo -n k3s kubectl get pods -A -o name 2>/dev/null | grep -i authentik | grep -m1 server' | tr -d '\r')
[ -z "$POD" ] && { echo "FAIL: no authentik-server pod (check: systemctl status k3s; sudo needs password — set OMV_SUDO_PASS)"; exit 2; }
TOK=$(ssh -o BatchMode=yes $HOST "sudo -n k3s kubectl exec ${POD#pod/} -n \$(sudo -n k3s kubectl get pod $POD -A -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null) -- ak create_token --user akadmin --identifier devin-agent --expiring=false 2>/dev/null" | tr -d '\r ' | tail -1)
[ -z "$TOK" ] && { echo "FAIL: token mint returned empty"; exit 1; }
grep -q '^AUTHENTIK_ADMIN_TOKEN=' "$ENV" 2>/dev/null && sed -i "s/^AUTHENTIK_ADMIN_TOKEN=.*/AUTHENTIK_ADMIN_TOKEN=$TOK/" "$ENV" || echo "AUTHENTIK_ADMIN_TOKEN=$TOK" >> "$ENV"
echo "TOKEN-WRITTEN to $ENV"
