#!/usr/bin/env bash
# Background poller: requests WhatsApp verification code when rate limit resets.
# Runs in a loop, checking every 10 minutes, until the code is successfully sent.
# Once sent, it writes the timestamp to a status file and exits.
#
# Usage: poll-rate-limit.sh <account_id> [SMS|VOICE] [language]
#
# Output:
#   - Prints progress to stdout/stderr
#   - Writes status to /tmp/whatsapp-verify-status.json when code is sent
#
# This script is designed to run in the background (e.g. via nohup or &).
# After it exits successfully, run auto-verify.sh to complete the flow.
set -euo pipefail

ACCOUNT_ID="${1:-}"
METHOD="${2:-SMS}"
LANGUAGE="${3:-en_US}"

if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id> [SMS|VOICE] [language]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATUS_FILE="/tmp/whatsapp-verify-status.json"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting rate-limit poller for account $ACCOUNT_ID"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Method: $METHOD, Language: $LANGUAGE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Poll interval: 600s (10 min)"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Max wait: 72 hours (432 attempts)"
echo ""

ATTEMPT=0
MAX_ATTEMPTS=432
SLEEP_SECONDS=600

while true; do
    ATTEMPT=$((ATTEMPT + 1))
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    
    echo "[$TIMESTAMP] Attempt $ATTEMPT/$MAX_ATTEMPTS..."
    
    RESPONSE=$(bash "$SCRIPT_DIR/request-code.sh" "$ACCOUNT_ID" "$METHOD" "$LANGUAGE" 2>/dev/null || echo '{"error":{"message":"script failed"}}')
    
    ERROR_CODE=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'error' in d:
        print(d['error'].get('code', 0))
    elif 'detail' in d:
        # SocialAuto wraps errors in 'detail'
        detail = d['detail']
        if '136024' in str(detail):
            print(136024)
        elif '133010' in str(detail):
            print(133010)
        else:
            print(0)
    else:
        print(0)
except:
    print(0)
" 2>/dev/null)
    
    if [[ "$ERROR_CODE" == "0" ]]; then
        echo "[$TIMESTAMP] ✅ Verification code sent successfully!"
        echo "{\"status\":\"code_sent\",\"timestamp\":\"$TIMESTAMP\",\"method\":\"$METHOD\",\"account_id\":\"$ACCOUNT_ID\"}" > "$STATUS_FILE"
        echo "[$TIMESTAMP] Status written to $STATUS_FILE"
        echo "[$TIMESTAMP] Now run: auto-verify.sh $ACCOUNT_ID <pin> $METHOD $LANGUAGE"
        exit 0
    fi
    
    if [[ "$ERROR_CODE" == "136024" ]]; then
        echo "[$TIMESTAMP] ⏳ Rate-limited (136024). Waiting ${SLEEP_SECONDS}s..."
    elif [[ "$ERROR_CODE" == "133010" ]]; then
        echo "[$TIMESTAMP] ⚠️ Account not registered (133010). Need to verify first."
        echo "{\"status\":\"not_registered\",\"timestamp\":\"$TIMESTAMP\",\"error\":133010}" > "$STATUS_FILE"
        exit 1
    else
        echo "[$TIMESTAMP] ❌ Error code $ERROR_CODE: $RESPONSE"
        echo "{\"status\":\"error\",\"timestamp\":\"$TIMESTAMP\",\"error_code\":$ERROR_CODE}" > "$STATUS_FILE"
        exit 1
    fi
    
    if [[ $ATTEMPT -ge $MAX_ATTEMPTS ]]; then
        echo "[$TIMESTAMP] ❌ Max attempts ($MAX_ATTEMPTS) reached. Rate limit window may not have reset."
        echo "{\"status\":\"timeout\",\"timestamp\":\"$TIMESTAMP\",\"attempts\":$ATTEMPT}" > "$STATUS_FILE"
        exit 1
    fi
    
    sleep $SLEEP_SECONDS
done
