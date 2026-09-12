#!/usr/bin/env bash
# Login to Instagram via the instagrapi sidecar using stored credentials.
# Handles 2FA and challenge responses by reporting the required action.
#
# Usage:
#   login.sh <account_id>
set -euo pipefail

ACCOUNT_ID="${1:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id>" >&2
  exit 1
fi

SIDECAR="http://localhost:8011"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check sidecar health
if ! bash "$SCRIPT_DIR/check-sidecar.sh" 2>/dev/null; then
  echo "❌ Sidecar is not healthy. Start the instagram-private-api container." >&2
  exit 1
fi

# Get credentials (username to stdout, pass file path to stderr)
CRED_STDERR_FILE=$(mktemp /tmp/ig-cred-err-XXXXXX)
USERNAME=$(bash "$SCRIPT_DIR/get-credentials.sh" "$ACCOUNT_ID" 2>"$CRED_STDERR_FILE")
RESULT=$?
if [[ $RESULT -ne 0 ]]; then
  echo "❌ Could not get credentials:" >&2
  cat "$CRED_STDERR_FILE" >&2
  rm -f "$CRED_STDERR_FILE" 2>/dev/null
  exit 1
fi

# Extract pass file path from stderr
PASS_FILE=$(grep "Password written to:" "$CRED_STDERR_FILE" | awk '{print $NF}')
rm -f "$CRED_STDERR_FILE" 2>/dev/null

PASSWORD=""
if [[ -n "$PASS_FILE" && -f "$PASS_FILE" ]]; then
  PASSWORD=$(cat "$PASS_FILE")
fi

if [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
  echo "❌ Missing username or password" >&2
  rm -f "$PASS_FILE" 2>/dev/null
  exit 1
fi

echo "=== Logging in to Instagram as $USERNAME ===" >&2

# Login via the sidecar (uses form-encoded data, not JSON)
LOGIN_RESULT=$(curl -s -X POST "${SIDECAR}/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=${USERNAME}&password=${PASSWORD}&locale=el_GR&timezone=10800" 2>/dev/null)

rm -f "$PASS_FILE" 2>/dev/null

# Parse the result
echo "$LOGIN_RESULT" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())

if d.get('session_id'):
    print('SESSION_ID:' + d['session_id'])
elif d.get('error') == 'challenge_required':
    print('CHALLENGE_REQUIRED')
    print(json.dumps(d.get('last_json', {})))
elif d.get('error') == 'two_factor_required':
    print('TWO_FACTOR_REQUIRED')
    print(json.dumps(d.get('last_json', {})))
elif d.get('error'):
    print('ERROR:' + str(d.get('error')))
    print(str(d.get('message', d.get('detail', ''))))
elif d.get('detail') and d.get('exc_type'):
    # Sidecar error format
    print('ERROR:' + str(d.get('exc_type', 'unknown')))
    print(str(d.get('detail', '')))
else:
    print('UNKNOWN')
    print(json.dumps(d))
" 2>/dev/null
