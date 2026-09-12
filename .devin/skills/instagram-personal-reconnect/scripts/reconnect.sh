#!/usr/bin/env bash
# Full Instagram personal account reconnect flow via instagrapi sidecar.
# Tries password login first, falls back to sessionid import if needed.
#
# Usage:
#   reconnect.sh <account_id> [sessionid]
set -euo pipefail

ACCOUNT_ID="${1:-}"
SESSIONID_ARG="${2:-}"

if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id> [sessionid]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================" >&2
echo "  Instagram Personal Reconnect" >&2
echo "  Account: $ACCOUNT_ID" >&2
echo "========================================" >&2
echo "" >&2

# Step 1: Check sidecar health
echo "=== Step 1: Check sidecar ===" >&2
if ! bash "$SCRIPT_DIR/check-sidecar.sh" 2>&1; then
  echo "" >&2
  echo "❌ Sidecar is not running." >&2
  echo "Start it with: docker compose up -d instagram-private-api" >&2
  exit 1
fi
echo "" >&2

# If sessionid provided, use it directly
if [[ -n "$SESSIONID_ARG" ]]; then
  echo "=== Using provided sessionid ===" >&2
  IMPORT_OUTPUT=$(bash "$SCRIPT_DIR/import-sessionid.sh" "$ACCOUNT_ID" "$SESSIONID_ARG" 2>&1)
  IMPORT_STATUS=$(echo "$IMPORT_OUTPUT" | head -1)
  if [[ "$IMPORT_STATUS" == SESSION_ID:* ]]; then
    SESSION_ID="${IMPORT_STATUS#SESSION_ID:}"
    echo "✅ Session imported" >&2
  else
    echo "❌ Session import failed: $IMPORT_STATUS" >&2
    echo "$IMPORT_OUTPUT" | tail -n +2 >&2
    exit 1
  fi
else
  # Step 2: Try password login
  echo "=== Step 2: Login via sidecar ===" >&2
  LOGIN_OUTPUT=$(bash "$SCRIPT_DIR/login.sh" "$ACCOUNT_ID" 2>&1)
  LOGIN_STATUS=$(echo "$LOGIN_OUTPUT" | head -1)

  case "$LOGIN_STATUS" in
    SESSION_ID:*)
      SESSION_ID="${LOGIN_STATUS#SESSION_ID:}"
      echo "✅ Login successful" >&2
      ;;
    CHALLENGE_REQUIRED)
      echo "⚠️ Challenge required. Instagram sent a security code via SMS/email." >&2
      echo -n "Enter the security code: " >&2
      read -r CODE
      LAST_JSON=$(echo "$LOGIN_OUTPUT" | tail -1)
      CHALLENGE_OUTPUT=$(bash "$SCRIPT_DIR/handle-challenge.sh" "$SESSION_ID" "$LAST_JSON" "$CODE" 2>&1)
      CHALLENGE_STATUS=$(echo "$CHALLENGE_OUTPUT" | head -1)
      if [[ "$CHALLENGE_STATUS" == SESSION_ID:* ]]; then
        SESSION_ID="${CHALLENGE_STATUS#SESSION_ID:}"
        echo "✅ Challenge resolved" >&2
      else
        echo "❌ Challenge resolution failed: $CHALLENGE_STATUS" >&2
        exit 1
      fi
      ;;
    TWO_FACTOR_REQUIRED)
      echo "⚠️ 2FA required. Enter the verification code from your authenticator app or SMS." >&2
      echo -n "Enter the code: " >&2
      read -r CODE
      TWOFAC_OUTPUT=$(bash "$SCRIPT_DIR/handle-2fa.sh" "$ACCOUNT_ID" "$CODE" 2>&1)
      TWOFAC_STATUS=$(echo "$TWOFAC_OUTPUT" | head -1)
      if [[ "$TWOFAC_STATUS" == SESSION_ID:* ]]; then
        SESSION_ID="${TWOFAC_STATUS#SESSION_ID:}"
        echo "✅ 2FA verified" >&2
      else
        echo "❌ 2FA verification failed: $TWOFAC_STATUS" >&2
        exit 1
      fi
      ;;
    ERROR:UnknownError)
      echo "⚠️ Password login blocked by Instagram version check." >&2
      echo "Falling back to sessionid import..." >&2
      echo "" >&2
      echo "Please provide the Instagram sessionid cookie:" >&2
      echo "  - Open instagram.com in a logged-in browser" >&2
      echo "  - DevTools → Application → Cookies → sessionid" >&2
      echo "  - Or use the Playwright MCP browser" >&2
      echo ""
      echo -n "Sessionid: " >&2
      read -r SESSIONID_COOKIE
      if [[ -z "$SESSIONID_COOKIE" ]]; then
        echo "❌ No sessionid provided. Aborting." >&2
        exit 1
      fi
      IMPORT_OUTPUT=$(bash "$SCRIPT_DIR/import-sessionid.sh" "$ACCOUNT_ID" "$SESSIONID_COOKIE" 2>&1)
      IMPORT_STATUS=$(echo "$IMPORT_OUTPUT" | head -1)
      if [[ "$IMPORT_STATUS" == SESSION_ID:* ]]; then
        SESSION_ID="${IMPORT_STATUS#SESSION_ID:}"
        echo "✅ Session imported" >&2
      else
        echo "❌ Session import failed: $IMPORT_STATUS" >&2
        exit 1
      fi
      ;;
    ERROR:*)
      echo "❌ Login failed: $LOGIN_STATUS" >&2
      echo "$LOGIN_OUTPUT" | tail -n +2 >&2
      exit 1
      ;;
    *)
      echo "❌ Unexpected login result: $LOGIN_STATUS" >&2
      echo "$LOGIN_OUTPUT" >&2
      exit 1
      ;;
  esac
fi
echo "" >&2

# Step 3: Save session
echo "=== Step 3: Save session ===" >&2
bash "$SCRIPT_DIR/save-session.sh" "$ACCOUNT_ID" "$SESSION_ID" 2>&1
echo "" >&2

# Step 4: Verify
echo "=== Step 4: Verify session ===" >&2
if bash "$SCRIPT_DIR/verify.sh" "$ACCOUNT_ID" 2>&1; then
  echo "" >&2
  echo "========================================" >&2
  echo "  ✅ Instagram personal account reconnected" >&2
  echo "========================================" >&2
else
  echo "" >&2
  echo "========================================" >&2
  echo "  ⚠️ Session saved but verification failed" >&2
  echo "  The session may need a few minutes to propagate." >&2
  echo "========================================" >&2
  exit 1
fi
