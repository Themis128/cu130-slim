#!/usr/bin/env bash
# Generate a batch of emojis/icons from a file of concepts (one per line).
#
# Usage: ./generate-batch.sh concepts.txt [style] [size] [output_dir]
#
# Example:
#   ./generate-batch.sh concepts.txt flat 512 ./emojis

set -euo pipefail

CONCEPTS_FILE="${1:?Usage: generate-batch.sh CONCEPTS_FILE [STYLE] [SIZE] [OUTPUT_DIR]}"
STYLE="${2:-flat}"
SIZE="${3:-512}"
OUTPUT_DIR="${4:-./emojis}"

mkdir -p "$OUTPUT_DIR"

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

# Login
TOKEN=$(curl -sf -X POST "$API_URL/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=${ADMIN_EMAIL}&password=${ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "✗ Login failed"
  exit 1
fi

# Switch team
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

# Read concepts
mapfile -t CONCEPTS < "$CONCEPTS_FILE"
CONCEPTS_JSON=$(python3 -c "
import json
with open('$CONCEPTS_FILE') as f:
    concepts = [line.strip() for line in f if line.strip()]
print(json.dumps(concepts))
")

echo "→ Generating ${#CONCEPTS[@]} emojis ($STYLE, ${SIZE}px)..."

PAYLOAD=$(python3 -c "
import json
concepts = json.loads('''$CONCEPTS_JSON''')
print(json.dumps({
    'concepts': concepts,
    'style': '$STYLE',
    'size': $SIZE,
    'background': 'transparent',
    'steps': 20,
    'cfg_scale': 8.0,
    'provider': 'local-diffusers',
    'enhance_prompt': True,
    'remove_bg': True
}))
")

RESULT=$(curl -sf -X POST "$API_URL/api/v1/ai/emoji/batch" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

echo "$RESULT" | python3 -c "
import sys, json, base64, os
d = json.load(sys.stdin)
emojis = d.get('emojis', [])
success = 0
for e in emojis:
    concept = e.get('concept', 'unknown')
    safe = concept.replace(' ', '-').replace('/', '-')
    if e.get('success'):
        img = base64.b64decode(e['image_base64'])
        path = os.path.join('$OUTPUT_DIR', f'{safe}.png')
        with open(path, 'wb') as f:
            f.write(img)
        print(f'  ✓ {concept} → {path} ({len(img)} bytes)')
        success += 1
    else:
        print(f'  ✗ {concept} — {e.get(\"error\",\"unknown error\")}')
print(f'\n{success}/{len(emojis)} emojis generated successfully')
"
