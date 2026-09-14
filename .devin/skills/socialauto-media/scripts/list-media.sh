#!/usr/bin/env bash
# List media library assets.
# Usage: list-media.sh [--type image|video|generated] [--limit 20] [--search "query"]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"
TYPE=""
LIMIT=20
SEARCH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --type) TYPE="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --search) SEARCH="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

ADMIN_EMAIL=$(grep -E '^SOCIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
ADMIN_PASS=$(grep -E '^SOCIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)

TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASS" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

PARAMS="page_size=$LIMIT"
[[ -n "$TYPE" ]] && PARAMS="$PARAMS&type=$TYPE"
if [[ -n "$SEARCH" ]]; then
  ENC=$(SEARCH="$SEARCH" python3 -c 'import os,urllib.parse; print(urllib.parse.quote(os.environ["SEARCH"]))')
  PARAMS="$PARAMS&search=$ENC"
fi

# Write to a temp file so Python never fights curl over stdin (pipe + heredoc clash).
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
curl -sf "$API/api/v1/media/assets?$PARAMS" \
  -H "Authorization: Bearer $TOKEN" \
  -o "$TMP"

python3 - "$TMP" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    d = json.load(fh)
items = d if isinstance(d, list) else d.get("assets", d.get("items", []))
if not items:
    print("No media found.")
    raise SystemExit(0)
for m in items:
    mime = m.get("mime_type", "?")
    name = m.get("filename", "?")
    size = m.get("size_bytes", 0) or 0
    sz = f"{size/1024:.1f}KB" if size < 1048576 else f"{size/1048576:.1f}MB"
    caption = (m.get("ai_caption") or "")[:30]
    print(f"{m['id']}  {mime:20s}  {sz:>10s}  {name}  {caption}")
PY
