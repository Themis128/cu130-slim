#!/usr/bin/env bash
# Trigger production Cloudless carousel webhook.
set -euo pipefail

PUBLISH=false
SLIDES=7
TOPIC=""
URL="${N8N_WEBHOOK_URL:-http://127.0.0.1:5678/webhook/cloudless-carousel}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --publish) PUBLISH="$2"; shift 2 ;;
    --slides) SLIDES="$2"; shift 2 ;;
    --topic) TOPIC="$2"; shift 2 ;;
    --url) URL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

BODY="$(python3 - <<PY
import json
pub = str("$PUBLISH").lower() in ("1", "true", "yes")
body = {"num_slides": int("$SLIDES"), "publish": pub}
topic = """$TOPIC"""
if topic.strip():
    body["topic"] = topic
print(json.dumps(body))
PY
)"

echo "POST $URL"
curl -sS -X POST "$URL" \
  -H 'Content-Type: application/json' \
  -d "$BODY" \
  --max-time 600 \
| python3 -m json.tool
