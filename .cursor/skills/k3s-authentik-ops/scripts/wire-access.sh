#!/bin/bash
set -euo pipefail
. /home/tbaltzakis/cu130-slim/.env
A=$CLOUDFLARE_ACCOUNT_ID; T=$CLOUDFLARE_API_TOKEN
ID=$(curl -sk -X POST "https://api.cloudflare.com/client/v4/accounts/$A/access/apps" -H "Authorization: Bearer $T" -H "Content-Type: application/json" -d '{"name":"authentik-admin-tailnet","domain":"auth.cloudless.gr/if/admin","type":"self_hosted","session_duration":"12h"}' | python3 -c "import json,sys;print(json.load(sys.stdin).get('result',{}).get('id',''))")
[ -n "$ID" ] && curl -sk -X POST "https://api.cloudflare.com/client/v4/accounts/$A/access/apps/$ID/policies" -H "Authorization: Bearer $T" -H "Content-Type: application/json" -d '{"name":"admin-email","decision":"allow","include":[{"email":{"email":"baltzakis.themis@gmail.com"}}]}' >/dev/null && echo "CF Access done" || echo "token lacks Zero Trust scope"
