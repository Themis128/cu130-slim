#!/usr/bin/env bash
# Generate a single emoji/icon via the SocialAuto API.
#
# Usage: ./generate-emoji.sh "concept" [style] [size] [background] [output_file]
#
# Examples:
#   ./generate-emoji.sh "happy cloud" kawaii 512 transparent
#   ./generate-emoji.sh "fire rocket" flat 256 white rocket.png
#   ./generate-emoji.sh "thumbs up" 3d 512 transparent

set -euo pipefail

CONCEPT="${1:?Usage: generate-emoji.sh CONCEPT [STYLE] [SIZE] [BACKGROUND] [OUTPUT]}"
STYLE="${2:-flat}"
SIZE="${3:-512}"
BACKGROUND="${4:-transparent}"
OUTPUT="${5:-emoji-$(echo "$CONCEPT" | tr ' ' '-').png}"

# Load environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

API_URL="${SOCIAL_API_URL:-http://localhost:8083}"
ADMIN_EMAIL="${SOCIAL_ADMIN_EMAIL:-admin@cloudless.gr}"
ADMIN_PASSWORD="${SOCIAL_ADMIN_PASSWORD:?SOCIAL_ADMIN_PASSWORD not set in .env}"

echo "→ Logging in as $ADMIN_EMAIL..."
TOKEN=$(curl -sf -X POST "$API_URL/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=${ADMIN_EMAIL}&password=${ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "✗ Login failed"
  exit 1
fi

# Switch to the first team that has accounts
TEAMS=$(curl -sf "$API_URL/api/v1/teams" -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
teams = json.load(sys.stdin)
for t in teams:
    print(t['id'])
" 2>/dev/null)

if [ -n "$TEAMS" ]; then
  TEAM_ID=$(echo "$TEAMS" | head -1)
  SWITCH_RESP=$(curl -sf -X POST "$API_URL/api/v1/auth/switch-team" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"team_id\": \"$TEAM_ID\"}" 2>/dev/null)
  NEW_TOKEN=$(echo "$SWITCH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
  if [ -n "$NEW_TOKEN" ]; then
    TOKEN="$NEW_TOKEN"
  fi
fi

echo "→ Generating emoji: '$CONCEPT' ($STYLE, ${SIZE}px, $BACKGROUND bg)..."

PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'concept': '$CONCEPT',
    'style': '$STYLE',
    'size': $SIZE,
    'background': '$BACKGROUND',
    'steps': 20,
    'cfg_scale': 8.0,
    'provider': 'local-diffusers',
    'enhance_prompt': True,
    'remove_bg': $( [ "$BACKGROUND" = "transparent" ] && echo "True" || echo "False" )
}))
")

RESULT=$(curl -sf -X POST "$API_URL/api/v1/ai/emoji/generate" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

echo "$RESULT" | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
img = base64.b64decode(d['image_base64'])
with open('$OUTPUT', 'wb') as f:
    f.write(img)
print(f'✓ Saved {len(img)} bytes to $OUTPUT')
print(f'  Provider: {d.get(\"provider\",\"?\")}')
print(f'  Enhanced prompt: {d.get(\"enhanced_prompt\",\"?\")[:100]}')
qc = d.get('quality_check')
if qc:
    print(f'  Quality: {qc.get(\"score\",\"?\")}/10 - {qc.get(\"issues\",\"\")[:80]}')
"
