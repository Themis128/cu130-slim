#!/usr/bin/env bash
# Get full brand profile.
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

curl -sf "$API/api/v1/brand" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Name: {d.get(\"name\",\"?\")}')
print(f'Industry: {d.get(\"industry\",\"?\")}')
print(f'Tagline: {d.get(\"tagline\",\"?\")}')
print(f'Website: {d.get(\"website_url\",\"?\")}')
print(f'Mission: {d.get(\"mission\",\"?\")}')
print(f'Values: {d.get(\"values\",[])}')
print(f'Positioning: {d.get(\"positioning_statement\",\"?\")[:100]}')
v = d.get('visual') or {}
print(f'Primary color: {v.get(\"primary_color\",\"?\")}')
print(f'Accent color: {v.get(\"accent_color\",\"?\")}')
print(f'Heading font: {v.get(\"font_heading\",\"?\")}')
print(f'Body font: {v.get(\"font_body\",\"?\")}')
"
