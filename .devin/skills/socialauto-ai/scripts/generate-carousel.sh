#!/usr/bin/env bash
# Generate a LinkedIn carousel (dry-run or publish).
# Usage: generate-carousel.sh "topic" --slides 7 [--publish false] [--account <id>]
# Note: For full custom slides, use the cloudless-carousel-pipeline skill.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

TOPIC="${1:?Usage: generate-carousel.sh \"topic\" --slides N [--publish false]}"
shift
SLIDES=7
PUBLISH="false"
ACCOUNT="f65df3e6-a5ef-4d70-ba6c-a568c1d46a7b"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --slides) SLIDES="$2"; shift 2 ;;
    --publish) PUBLISH="$2"; shift 2 ;;
    --account) ACCOUNT="$2"; shift 2 ;;
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
print(json.dumps({
  'topic': '''$TOPIC''',
  'num_slides': int('$SLIDES'),
  'tone': 'clear and friendly',
  'include_cta': True,
  'text_model': '@cf/meta/llama-3.2-3b-instruct',
  'txt2img_model': '@cf/black-forest-labs/flux-1-schnell',
  'target_account_id': '$ACCOUNT',
  'publish': str('$PUBLISH').lower() in ('1', 'true', 'yes'),
}))
")

echo "Generating carousel: $TOPIC ($SLIDES slides, publish=$PUBLISH)"
curl -sf -X POST "$API/api/v1/ai/run-carousel-and-publish" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" --max-time 280 \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Status: {d.get(\"status\",\"?\")}')
print(f'Post ID: {d.get(\"post_id\",\"?\")}')
print(f'Media IDs: {d.get(\"media_ids\",[])}')
print(f'AI Title: {d.get(\"ai_title\",\"?\")}')
print(f'Slides: {len(d.get(\"slides\",[]))}')
for i, s in enumerate(d.get('slides',[])):
    print(f'  Slide {i+1} ({s.get(\"slide_type\",\"?\")}): {s.get(\"title\",\"?\")[:50]}')
if d.get('platform_url'):
    print(f'LinkedIn URL: {d[\"platform_url\"]}')
"
