#!/bin/bash
# ts-authkey.sh — mint a preauthorized auth key via API + join a node non-interactively.
# Usage: ts-authkey.sh [ssh-host or "local"]  — mints tskey-auth-* and runs tailscale up --auth-key.
K=$(cut -d= -f2 /home/tbaltzakis/.config/secrets/tailscale-api 2>/dev/null)
[ -z "$K" ] && { echo "no TAILSCALE_API_KEY"; exit 2; }
AK=$(python3 -c "
import json,urllib.request
r=urllib.request.Request(\"https://api.tailscale.com/api/v2/tailnet/-/keys\",method=\"POST\",
  data=json.dumps({\"capabilities\":{\"devices\":{\"create\":{\"reusable\":False,\"preauthorized\":True}}},\"expirySeconds\":3600}).encode(),
  headers={\"Authorization\":\"Bearer $K\",\"Content-Type\":\"application/json\"})
print(json.load(urllib.request.urlopen(r,timeout=20))[\"key\"])")
echo "key minted (expires 1h, preauthorized)"
if [ "$1" = "local" ] || [ -z "$1" ]; then
  tailscale up --auth-key="$AK" 2>&1 | tail -3
else
  ssh -o BatchMode=yes "$1" "sudo tailscale up --auth-key=$AK" 2>&1 | tail -3
fi
