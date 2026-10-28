#!/usr/bin/env bash
# wa_verify.sh — WhatsApp phone verify via Graph + ADB SMS read.
#   wa_verify.sh            # request code → poll SMS → verify
#   wa_verify.sh <code>     # manual fallback: supply the code yourself
set -e
CID="${WHATSAPP_PHONE_NUMBER_ID:?}"
TOK="${WHATSAPP_ACCESS_TOKEN:?}"
CODE="${1:-}"

if [ -z "$CODE" ]; then
  echo "→ requesting SMS code for phone_number_id=$CID"
  curl -s "https://graph.facebook.com/v21.0/$CID/request_code" \
    -H "Authorization: Bearer $TOK" \
    -d "code_method=${WA_CODE_METHOD:-SMS}&language=en_US"
  echo "→ polling SMS inbox (ADB) for the code…"
  for i in $(seq 1 24); do
    CODE=$(adb shell content query --uri content://sms/inbox --sort "date DESC" 2>/dev/null \
           | grep -oE "[0-9]{6}" | head -1)
    [ -n "$CODE" ] && break
    sleep 10
  done
fi
[ -z "$CODE" ] && { echo "no code — run: wa_verify.sh <code>"; exit 1; }
echo "→ verify_code $CODE"
curl -s "https://graph.facebook.com/v21.0/$CID/verify_code" \
  -H "Authorization: Bearer $TOK" -d "code=$CODE"
