#!/bin/bash
# recovery-link.sh — mint a one-use recovery invite for a user via the authentik API.
# Usage: recovery-link.sh <username-or-email> [expiry-hours]
set -euo pipefail
T=$(grep '^AUTHENTIK_ADMIN_TOKEN=' /home/tbaltzakis/cu130-slim/.env | cut -d= -f2)
U=$1; H=${2:-48}
curl -sk -X POST "http://100.74.191.58:30910/api/v3/stages/invitation/invites/" \
 -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
 -d "{\"name\":\"recovery-$U\",\"flow\":\"cloudless-recovery\",\"expires\":\"$(date -u -d "+${H}h" +%FT%TZ)\",\"single_use\":true}" | python3 -m json.tool | grep -E '"(pk|expires|flow)"'
echo "open: https://auth.cloudless.gr/if/flow/cloudless-recovery/?flow_token=<pk>"
