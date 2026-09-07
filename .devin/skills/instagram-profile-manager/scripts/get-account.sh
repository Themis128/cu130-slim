#!/usr/bin/env bash
# Get current authenticated account info
# Usage: get-account.sh [session_id]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${INSTAGRAM_SIDECAR_URL:-http://localhost:8011}"
SID="${1:-}"

HEADERS=()
if [[ -n "$SID" ]]; then
  HEADERS+=(-H "X-Session-ID: $SID")
fi

curl -sf "$API/account" "${HEADERS[@]}" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'Username: {d.get(\"username\", \"N/A\")}')
    print(f'Full name: {d.get(\"full_name\", \"N/A\")}')
    print(f'PK: {d.get(\"pk\", \"N/A\")}')
    print(f'Biography: {d.get(\"biography\", \"N/A\")}')
    print(f'External URL: {d.get(\"external_url\", \"N/A\")}')
    print(f'Followers: {d.get(\"follower_count\", \"N/A\")}')
    print(f'Following: {d.get(\"following_count\", \"N/A\")}')
    print(f'Media count: {d.get(\"media_count\", \"N/A\")}')
    print(f'Is private: {d.get(\"is_private\", \"N/A\")}')
    print(f'Is business: {d.get(\"is_business\", \"N/A\")}')
    print(f'Profile pic URL: {str(d.get(\"profile_pic_url\", \"N/A\"))[:80]}...')
except Exception as e:
    print(f'Error: {e}')
    print(sys.stdin.read()[:200])
"
