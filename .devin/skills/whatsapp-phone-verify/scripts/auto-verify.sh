#!/usr/bin/env bash
# Auto-verify WhatsApp phone number: polls until rate limit resets, requests code,
# waits for user to provide it, verifies, and registers.
#
# Usage: auto-verify.sh <account_id> [6-digit-pin] [SMS|VOICE] [language]
#
# If PIN is not provided, a default of "123456" is used (change it later via API).
# The script will:
#   1. Check current status (skip if already VERIFIED)
#   2. Poll request_code every 10 minutes until the 72h rate limit window resets
#   3. When code is sent successfully, prompt user to enter the 6-digit code
#   4. Verify the code
#   5. Register the number with the PIN
#   6. Confirm VERIFIED status
set -euo pipefail

ACCOUNT_ID="${1:-}"
PIN="${2:-123456}"
METHOD="${3:-SMS}"
LANGUAGE="${4:-en_US}"

if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id> [6-digit-pin] [SMS|VOICE] [language]" >&2
  echo "" >&2
  echo "Example:" >&2
  echo "  $0 77f17091-3639-4633-b04d-0a3345dd7d3a 482913 SMS en_US" >&2
  echo "  $0 77f17091-3639-4633-b04d-0a3345dd7d3a  # uses default PIN 123456" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo " WhatsApp Phone Auto-Verification"
echo "=========================================="
echo "Account:  $ACCOUNT_ID"
echo "PIN:      $PIN"
echo "Method:   $METHOD"
echo "Language: $LANGUAGE"
echo "=========================================="
echo ""

# Step 1: Check current status
echo "[1/6] Checking current phone status..."
STATUS=$(bash "$SCRIPT_DIR/check-phone-status.sh" "$ACCOUNT_ID" 2>/dev/null)
echo "$STATUS" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    v = d.get('code_verification_status', 'UNKNOWN')
    print(f'  code_verification_status: {v}')
    if v == 'VERIFIED':
        print('  Phone is already VERIFIED — no action needed.')
        sys.exit(0)
    print(f'  display_phone_number: {d.get(\"display_phone_number\", \"?\")}')
    print(f'  quality_rating: {d.get(\"quality_rating\", \"?\")}')
except Exception:
    print('  (could not parse status)')
    sys.exit(1)
sys.exit(0)
" || exit 0

# Step 2: Poll request_code until rate limit resets
echo ""
echo "[2/6] Requesting verification code (polling if rate-limited)..."
ATTEMPT=0
MAX_ATTEMPTS=432  # 72 hours / 10 minutes = 432 attempts
SLEEP_SECONDS=600  # 10 minutes between retries

while true; do
    ATTEMPT=$((ATTEMPT + 1))
    echo "  Attempt $ATTEMPT/$MAX_ATTEMPTS at $(date '+%Y-%m-%d %H:%M:%S')..."
    
    RESPONSE=$(bash "$SCRIPT_DIR/request-code.sh" "$ACCOUNT_ID" "$METHOD" "$LANGUAGE" 2>/dev/null)
    
    # Check if request succeeded (no error in response)
    ERROR=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'error' in d or 'detail' in d:
        msg = d.get('error', {}).get('message', '') or d.get('detail', '')
        code = d.get('error', {}).get('code', 0)
        print(f'{code}:{msg}')
    else:
        print('OK')
except:
    print('parse_error')
" 2>/dev/null)
    
    if [[ "$ERROR" == "OK" ]]; then
        echo "  ✅ Verification code sent successfully via $METHOD!"
        break
    fi
    
    if [[ "$ERROR" == *"136024"* ]] || [[ "$ERROR" == *"rate"*"limit"* ]] || [[ "$ERROR" == *"too many"* ]]; then
        echo "  ⏳ Rate-limited (error 136024). Waiting ${SLEEP_SECONDS}s before retry..."
        if [[ $ATTEMPT -ge $MAX_ATTEMPTS ]]; then
            echo "  ❌ Max attempts reached. Rate limit window may not have reset yet."
            echo "  Try again later with: $0 $ACCOUNT_ID $PIN $METHOD $LANGUAGE"
            exit 1
        fi
        sleep $SLEEP_SECONDS
    elif [[ "$ERROR" == *"136024"* ]]; then
        echo "  ⏳ Rate-limited. Waiting ${SLEEP_SECONDS}s..."
        sleep $SLEEP_SECONDS
    else
        echo "  ❌ Unexpected error: $ERROR"
        echo "  Response: $RESPONSE"
        exit 1
    fi
done

# Step 3: Prompt user for the code
echo ""
echo "[3/6] Enter the 6-digit verification code received on your phone."
echo "  (Check SMS from WhatsApp / listen for voice call)"
echo ""
read -p "  Enter 6-digit code: " CODE

if [[ -z "$CODE" || ${#CODE} -ne 6 ]]; then
    echo "  ❌ Invalid code. Must be 6 digits."
    exit 1
fi

# Step 4: Verify the code
echo ""
echo "[4/6] Verifying code $CODE..."
VERIFY_RESPONSE=$(bash "$SCRIPT_DIR/verify-code.sh" "$ACCOUNT_ID" "$CODE" 2>/dev/null)
echo "$VERIFY_RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'error' in d or 'detail' in d:
        msg = d.get('error', {}).get('message', '') or d.get('detail', '')
        print(f'  ❌ Verify failed: {msg}')
        sys.exit(1)
    print(f'  ✅ Code verified successfully!')
except Exception as e:
    print(f'  ❌ Error: {e}')
    sys.exit(1)
" || exit 1

# Step 5: Register the number
echo ""
echo "[5/6] Registering phone number with PIN $PIN..."
REGISTER_RESPONSE=$(bash "$SCRIPT_DIR/register-phone.sh" "$ACCOUNT_ID" "$PIN" 2>/dev/null)
echo "$REGISTER_RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'error' in d or 'detail' in d:
        msg = d.get('error', {}).get('message', '') or d.get('detail', '')
        print(f'  ❌ Register failed: {msg}')
        sys.exit(1)
    print(f'  ✅ Phone registered successfully!')
except Exception as e:
    print(f'  ❌ Error: {e}')
    sys.exit(1)
" || exit 1

# Step 6: Confirm status
echo ""
echo "[6/6] Confirming final status..."
sleep 3
bash "$SCRIPT_DIR/check-phone-status.sh" "$ACCOUNT_ID" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    v = d.get('code_verification_status', 'UNKNOWN')
    print(f'  code_verification_status: {v}')
    if v == 'VERIFIED':
        print()
        print('  🎉 WhatsApp phone verification COMPLETE!')
        print('  The phone number is now registered for Cloud API use.')
    else:
        print(f'  ⚠️ Status is {v} — may need a few seconds to propagate.')
except:
    print('  (could not parse status)')
"

echo ""
echo "=========================================="
echo " Auto-Verification Complete"
echo "=========================================="
