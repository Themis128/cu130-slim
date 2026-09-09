#!/usr/bin/env bash
# Update brand visual identity (colors, fonts, logo).
# Usage: update-brand-visual.sh <primary_color> <accent_color> <heading_font> <body_font> [logo_media_id]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

PRIMARY="${1:?Usage: update-brand-visual.sh <primary_color> <accent_color> <heading_font> <body_font> [logo_media_id]}"
ACCENT="${2:?Usage: update-brand-visual.sh <primary_color> <accent_color> <heading_font> <body_font> [logo_media_id]}"
HEADING="${3:?Usage: update-brand-visual.sh <primary_color> <accent_color> <heading_font> <body_font> [logo_media_id]}"
BODY="${4:?Usage: update-brand-visual.sh <primary_color> <accent_color> <heading_font> <body_font> [logo_media_id]}"
LOGO_ID="${5:-}"

BODY=$(python3 -c "
import json
d = {
    'primary_color': '$PRIMARY',
    'accent_color': '$ACCENT',
    'font_heading': '$HEADING',
    'font_body': '$BODY',
}
if '$LOGO_ID':
    d['logo_url'] = f'/api/v1/media/view?path=$LOGO_ID'
print(json.dumps(d))
")

curl -sf -X PUT "$API/api/v1/brand/visual" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$BODY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Visual updated: primary={d.get(\"primary_color\")}, accent={d.get(\"accent_color\")}')
print(f'Fonts: {d.get(\"font_heading\")} / {d.get(\"font_body\")}')
if d.get('logo_url'):
    print(f'Logo: {d.get(\"logo_url\")}')
"
