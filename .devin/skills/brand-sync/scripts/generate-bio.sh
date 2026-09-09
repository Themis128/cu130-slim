#!/usr/bin/env bash
# Generate the brand bio text from the SocialAuto brand profile.
# Outputs the bio text to stdout.
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
if d is None:
    print('No brand profile set.')
    sys.exit(1)

tagline = d.get('tagline', '')
mission = d.get('mission', '')
website = d.get('website_url', '')
website_short = website.replace('https://', '').replace('http://', '').rstrip('/') if website else ''

# Extract first sentence of mission for bio
mission_short = mission.split('.')[0] + '.' if mission and '.' in mission else (mission or '')

# Build bio
lines = []
if tagline:
    lines.append(tagline)
if mission_short:
    lines.append(mission_short.strip())
if website_short:
    lines.append(f'↓ {website_short}')

bio = '\n'.join(lines)
print(bio)
"
