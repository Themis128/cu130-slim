#!/usr/bin/env bash
# Full WhatsApp phone verification flow with rate-limit handling.
# Orchestrates: check-status → wait-and-request → prompt for code → verify-and-register → confirm
#
# Usage:
#   full-flow.sh <account_id> [SMS|VOICE] [language] [pin]
set -euo pipefail

ACCOUNT_ID="${1:-}"
CODE_METHOD="${2:-SMS}"
LANGUAGE="${3:-en_US}"
PIN="${4:-}"

if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id> [SMS|VOICE] [language] [pin]" >&2
  echo "" >&2
  echo "Example:" >&2
  echo "  $0 77f17091-3639-4633-b04d-0a3345dd7d3a SMS el_GR 123456" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================" >&2
echo "  WhatsApp Phone Verification" >&2
echo "  Account: $ACCOUNT_ID" >&2
echo "  Method:  $CODE_METHOD" >&2
echo "  Language: $LANGUAGE" >&2
echo "========================================" >&2
echo "" >&2

# Step 1: Check current status
echo "=== Step 1: Check current status ===" >&2
bash "$SCRIPT_DIR/check-status.sh" "$ACCOUNT_ID" 2>&1
echo "" >&2

# Step 2: Wait for cooldown and request code
echo "=== Step 2: Request verification code ===" >&2
if ! bash "$SCRIPT_DIR/wait-and-request.sh" "$ACCOUNT_ID" "$CODE_METHOD" "$LANGUAGE" 2>&1; then
  echo "" >&2
  echo "❌ Could not request verification code." >&2
  echo "The rate limit may still be active. Try again later." >&2
  exit 1
fi
echo "" >&2

# Step 3: Prompt user for the code
echo "=== Step 3: Enter verification code ===" >&2
echo "A ${CODE_METHOD} verification code was sent to the phone number." >&2
echo -n "Enter the 6-digit code: " >&2
read -r CODE
echo "" >&2

if [[ -z "$CODE" ]]; then
  echo "❌ No code entered. Aborting." >&2
  exit 1
fi

# Step 4: Verify and register
echo "=== Step 4: Verify code and register ===" >&2
bash "$SCRIPT_DIR/verify-and-register.sh" "$ACCOUNT_ID" "$CODE" "$PIN" 2>&1
RESULT=$?
echo "" >&2

if [[ $RESULT -eq 0 ]]; then
  echo "========================================" >&2
  echo "  ✅ WhatsApp phone verification complete" >&2
  echo "========================================" >&2
else
  echo "========================================" >&2
  echo "  ❌ WhatsApp phone verification failed" >&2
  echo "========================================" >&2
fi

exit $RESULT
