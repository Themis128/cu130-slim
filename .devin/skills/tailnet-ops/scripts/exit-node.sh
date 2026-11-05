#!/bin/bash
# exit-node.sh — set --advertise-exit-node on a host over tailnet SSH + approve routes via API.
# Usage: exit-node.sh <ssh-host>
H=$1; P=$(tr -d "\r\n" < /home/tbaltzakis/.config/secrets/omv-sudo 2>/dev/null)
ssh -o BatchMode=yes tbaltzakis@$H "echo $P | sudo -S sh -c 'tailscale set --advertise-exit-node && tailscale up 2>&1 | tail -1'"
sleep 15
T=$(cut -d= -f2 /home/tbaltzakis/.config/secrets/tailscale-api)
python3 -c "
import json,urllib.request
r=urllib.request.Request(\"https://api.tailscale.com/api/v2/tailnet/themis128.github/devices\",headers={\"Authorization\":\"Bearer $T\"})
for d in json.load(urllib.request.urlopen(r,timeout=20))[\"devices\"]:
    if \"$H\" in \" \".join(d.get(\"addresses\",[])) or any(x in d[\"name\"] for x in [\"$H\"]):
        adv=d.get(\"advertisedRoutes\") or []
        print(d[\"name\"],\"adv=\",adv)
        if adv:
            r2=urllib.request.Request(\"https://api.tailscale.com/api/v2/device/\"+d[\"id\"]+\"/routes\",method=\"POST\",data=json.dumps({\"routes\":adv}).encode(),headers={\"Authorization\":\"Bearer $T\",\"Content-Type\":\"application/json\"})
            urllib.request.urlopen(r2,timeout=20); print(\"routes approved\")"
