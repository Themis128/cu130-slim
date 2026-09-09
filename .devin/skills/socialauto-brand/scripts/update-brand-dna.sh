#!/usr/bin/env bash
# Update brand DNA (name, industry, tagline, mission, values, website).
# Usage: update-brand-dna.sh <name> <tagline> <website> [mission]
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

NAME="${1:?Usage: update-brand-dna.sh <name> <tagline> <website> [mission]}"
TAGLINE="${2:?Usage: update-brand-dna.sh <name> <tagline> <website> [mission]}"
WEBSITE="${3:?Usage: update-brand-dna.sh <name> <tagline> <website> [mission]}"
MISSION="${4:-}"

BODY=$(python3 -c "
import json
d = {'name': '$NAME', 'tagline': '$TAGLINE', 'website_url': '$WEBSITE'}
if '$MISSION':
    d['mission'] = '$MISSION'
print(json.dumps(d))
")

curl -sf -X PUT "$API/api/v1/brand" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$BODY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Brand updated: {d.get(\"name\",\"?\")} - {d.get(\"tagline\",\"?\")}')
"
