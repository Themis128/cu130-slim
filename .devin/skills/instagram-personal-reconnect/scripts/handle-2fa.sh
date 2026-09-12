#!/usr/bin/env bash
# Complete 2FA login with a verification code.
#
# Usage:
#   handle-2fa.sh <account_id> <verification_code>
set -euo pipefail

ACCOUNT_ID="${1:-}"
CODE="${2:-}"

if [[ -z "$ACCOUNT_ID" || -z "$CODE" ]]; then
  echo "Usage: $0 <account_id> <verification_code>" >&2
  exit 1
fi

SIDECAR="http://localhost:8011"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Get credentials
CRED_STDERR_FILE=$(mktemp /tmp/ig-cred-err-XXXXXX)
USERNAME=$(bash "$SCRIPT_DIR/get-credentials.sh" "$ACCOUNT_ID" 2>"$CRED_STDERR_FILE")
PASS_FILE=$(grep "Password written to:" "$CRED_STDERR_FILE" | awk '{print $NF}')
rm -f "$CRED_STDERR_FILE" 2>/dev/null
PASSWORD=$(cat "$PASS_FILE" 2>/dev/null || echo "")
rm -f "$PASS_FILE" 2>/dev/null

if [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
  echo "❌ Missing username or password" >&2
  exit 1
fi

echo "=== Completing 2FA login ===" >&2

# Login with 2FA code (form-encoded)
RESULT=$(curl -s -X POST "${SIDECAR}/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=${USERNAME}&password=${PASSWORD}&verification_code=${CODE}&locale=el_GR&timezone=10800" 2>/dev/null)

echo "$RESULT" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
if d.get('session_id'):
    print('SESSION_ID:' + d['session_id'])
elif d.get('error'):
    print('ERROR:' + str(d.get('error')))
    print(str(d.get('message', d.get('detail', ''))))
elif d.get('detail') and d.get('exc_type'):
    print('ERROR:' + str(d.get('exc_type', 'unknown')))
    print(str(d.get('detail', '')))
else:
    print(json.dumps(d))
" 2>/dev/null
