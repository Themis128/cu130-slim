#!/usr/bin/env bash
# Login to social-api and run CF carousel pipeline.
# Never prints passwords or tokens.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

PUBLISH=false
SLIDES=7
TOPIC="${CLOUDLESS_CAROUSEL_TOPIC:-How cloudless.gr helps teams ship serverless apps without managing servers}"
ACCOUNT="${CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID:-4a8d9440-47d2-4bda-bd11-3776fd9022ba}"
API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --publish) PUBLISH="$2"; shift 2 ;;
    --slides) SLIDES="$2"; shift 2 ;;
    --topic) TOPIC="$2"; shift 2 ;;
    --account) ACCOUNT="$2"; shift 2 ;;
    --api) API="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "Missing .env in $ROOT" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
# shellcheck source=/dev/null
source .env
set +a

: "${SOCIAL_ADMIN_EMAIL:?SOCIAL_ADMIN_EMAIL required}"
: "${SOCIAL_ADMIN_PASSWORD:?SOCIAL_ADMIN_PASSWORD required}"

TOKEN="$(
  curl -sf -X POST "$API/api/v1/auth/login" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode "username=$SOCIAL_ADMIN_EMAIL" \
    --data-urlencode "password=$SOCIAL_ADMIN_PASSWORD" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'
)"

BODY="$(python3 - <<PY
import json
print(json.dumps({
  "topic": """$TOPIC""",
  "num_slides": int("$SLIDES"),
  "tone": "clear and friendly",
  "include_cta": True,
  "text_model": "@cf/meta/llama-3.2-3b-instruct",
  "txt2img_model": "@cf/black-forest-labs/flux-1-schnell",
  "img2img_model": "@cf/runwayml/stable-diffusion-v1-5-img2img",
  "strength": 0.42,
  "target_account_id": "$ACCOUNT",
  "publish": str("$PUBLISH").lower() in ("1", "true", "yes"),
}))
PY
)"

curl -sf -X POST "$API/api/v1/ai/run-carousel-and-publish" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  --max-time 600 \
| python3 -m json.tool
