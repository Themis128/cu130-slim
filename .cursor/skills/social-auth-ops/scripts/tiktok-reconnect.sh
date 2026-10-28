#!/usr/bin/env bash
# tiktok-reconnect.sh — print the TikTok OAuth authorize URL for re-auth.
# Refresh tokens die at 365d; a dead refresh needs full re-authorization.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2- | tr -d '\r')
PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2- | tr -d '\r')
API="${SOCIAL_API:-http://localhost:8083}"
TOKEN=$(curl -s -X POST "$API/api/v1/auth/login" -H 'Content-Type: application/x-www-form-urlencoded' -d "username=$EMAIL&password=$PASS" | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
TEAM=$(curl -s "$API/api/v1/teams" -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys;d=json.load(sys.stdin);print((d[0] if isinstance(d,list) else d.get('items',[{}])[0]).get('id',''))")
echo "Open this URL, approve (user.info.basic video.publish video.upload):"
curl -s -X POST "$API/api/v1/accounts/connect/tiktok?team_id=$TEAM" -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys;print(json.load(sys.stdin).get('authorization_url') or json.load(sys.stdin))"
