#!/usr/bin/env bash
# Resolve a challenge (SMS/email verification) with a security code.
#
# Usage:
#   handle-challenge.sh <session_id> <last_json> <security_code>
set -euo pipefail

SESSION_ID="${1:-}"
LAST_JSON="${2:-}"
CODE="${3:-}"

if [[ -z "$SESSION_ID" || -z "$LAST_JSON" || -z "$CODE" ]]; then
  echo "Usage: $0 <session_id> <last_json> <security_code>" >&2
  exit 1
fi

SIDECAR="http://localhost:8011"

echo "=== Resolving challenge ===" >&2

RESULT=$(curl -s -X POST "${SIDECAR}/auth/challenge/resolve" \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: ${SESSION_ID}" \
  -d "{\"last_json\": ${LAST_JSON}, \"security_code\": \"${CODE}\"}" 2>/dev/null)

echo "$RESULT" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
if d.get('session_id'):
    print('SESSION_ID:' + d['session_id'])
elif d.get('status') == 'ok':
    print('RESOLVED')
elif d.get('error'):
    print('ERROR:' + str(d.get('error')))
else:
    print(json.dumps(d))
" 2>/dev/null
