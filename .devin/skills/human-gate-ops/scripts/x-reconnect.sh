#!/usr/bin/env bash
# x-reconnect.sh — mint the X OAuth2 reconnect URL for @TBaltzakis.
# Logs in via /api/v1/auth/login (OAuth2 form), grabs team_id, hits
# POST /connect/twitter → prints the authorize URL to click.
set -euo pipefail
ENV="${ENV:-$(dirname "$0")/../../../../.env}"
EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' "$ENV" | cut -d= -f2-)
PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' "$ENV" | cut -d= -f2-)
API="${SOCIAL_API:-http://localhost:8083}"
TOKEN=$(curl -s -X POST "$API/api/v1/auth/login" -H 'Content-Type: application/x-www-form-urlencoded' -d "username=$EMAIL&password=$PASS" | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
TEAM=$(curl -s "$API/api/v1/teams" -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys;d=json.load(sys.stdin);print((d[0] if isinstance(d,list) else d.get('items',[{}])[0]).get('id',''))")
curl -s -X POST "$API/api/v1/accounts/connect/twitter?team_id=$TEAM" -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin)['authorization_url'])"
