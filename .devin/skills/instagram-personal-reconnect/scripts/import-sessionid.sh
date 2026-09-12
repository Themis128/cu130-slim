#!/usr/bin/env bash
# Import an Instagram session by sessionid cookie (bypasses password login).
# Use this when the password login is blocked by Instagram's version check.
#
# Usage:
#   import-sessionid.sh <account_id> <sessionid>
set -euo pipefail

ACCOUNT_ID="${1:-}"
SESSIONID_COOKIE="${2:-}"

if [[ -z "$ACCOUNT_ID" || -z "$SESSIONID_COOKIE" ]]; then
  echo "Usage: $0 <account_id> <sessionid>" >&2
  exit 1
fi

SIDECAR="http://localhost:8011"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check sidecar health
if ! bash "$SCRIPT_DIR/check-sidecar.sh" 2>/dev/null; then
  echo "❌ Sidecar is not healthy." >&2
  exit 1
fi

echo "=== Importing session via sessionid ===" >&2

# Login via sessionid (form-encoded, field name is "sessionid")
RESULT=$(curl -s -X POST "${SIDECAR}/auth/login/by/sessionid" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "sessionid=${SESSIONID_COOKIE}" 2>/dev/null)

# The sidecar returns the session_id as a plain string on success
echo "$RESULT" | python3 -c "
import sys, json
raw = sys.stdin.read().strip()
try:
    d = json.loads(raw)
    if isinstance(d, str):
        # Plain string = session_id
        print('SESSION_ID:' + d)
    elif isinstance(d, dict):
        if d.get('session_id'):
            print('SESSION_ID:' + d['session_id'])
        elif d.get('error'):
            print('ERROR:' + str(d.get('error')))
            print(str(d.get('message', d.get('detail', ''))))
        elif d.get('detail') and d.get('exc_type'):
            print('ERROR:' + str(d.get('exc_type', 'unknown')))
            print(str(d.get('detail', '')))
        else:
            print('UNKNOWN')
            print(json.dumps(d))
    else:
        print('UNKNOWN')
        print(raw)
except json.JSONDecodeError:
    # Not JSON — might be a plain string session_id
    if raw and len(raw) > 10:
        print('SESSION_ID:' + raw.strip('\"'))
    else:
        print('ERROR:empty_response')
" 2>/dev/null
