#!/usr/bin/env bash
# Generate an AI image and save to media library.
# Uses the app's fallback chain: Local Diffusers (SD 1.5, GPU) → Cloudflare Workers AI.
# Usage: generate-image.sh "prompt text" [--steps N] [--width W] [--height H] [--negative "..."]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

PROMPT="${1:?Usage: generate-image.sh \"prompt\" [--steps N] [--width W] [--height H] [--negative \"...\"]}"
shift

STEPS=25
WIDTH=1024
HEIGHT=1024
NEGATIVE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --steps) STEPS="$2"; shift 2 ;;
    --width) WIDTH="$2"; shift 2 ;;
    --height) HEIGHT="$2"; shift 2 ;;
    --negative) NEGATIVE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

BODY=$(python3 -c "
import json
opts = {'width': $WIDTH, 'height': $HEIGHT, 'steps': $STEPS, 'cfg_scale': 7.5}
if '''$NEGATIVE''':
    opts['negative_prompt'] = '''$NEGATIVE'''
print(json.dumps({'prompt': '''$PROMPT''', 'options': opts}))
")

echo "Generating image (Local Diffusers primary, CF fallback): $PROMPT"
curl -sf -X POST "$API/api/v1/media/generate-image" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Media ID: {d.get(\"id\",\"?\")}')
print(f'Filename: {d.get(\"filename\",\"?\")}')
md = d.get('meta_data') or {}
print(f'Provider: {md.get(\"inference_provider\",\"?\")}')
print(f'Model: {md.get(\"inference_model\",\"?\")}')
qs = md.get('quality_score') or {}
if qs:
    print(f'Quality: overall={qs.get(\"overall\")}/100 sharp={qs.get(\"sharpness\")} bright={qs.get(\"brightness\")} contrast={qs.get(\"contrast\")}')
    if md.get('quality_failed'):
        print(f'Quality: FLAGGED (below threshold)')
"
