#!/bin/bash
# phone-kill.sh — instant tailnet kill-switch for the phone (theft/loss).
# Removes the device from the tailnet immediately — tailnet access dies at once.
K=$(cut -d= -f2 /home/tbaltzakis/.config/secrets/tailscale-api 2>/dev/null)
[ -z "$K" ] && { echo "no TAILSCALE_API_KEY"; exit 2; }
python3 -c "
import json,urllib.request
r=urllib.request.Request(\"https://api.tailscale.com/api/v2/tailnet/themis128.github/devices\",headers={\"Authorization\":\"Bearer $K\"})
for d in json.load(urllib.request.urlopen(r,timeout=20))[\"devices\"]:
    if \"galaxy\" in d[\"name\"]:
        urllib.request.urlopen(urllib.request.Request(\"https://api.tailscale.com/api/v2/device/\"+d[\"id\"],method=\"DELETE\",headers={\"Authorization\":\"Bearer $K\"}),timeout=20)
        print(d[\"name\"],\"REVOKED\")"
