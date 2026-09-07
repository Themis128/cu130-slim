#!/usr/bin/env bash
# Get any Instagram user's profile
# Usage: get-user.sh [session_id] <username>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="${INSTAGRAM_SIDECAR_URL:-http://localhost:8011}"
SID="${1:?Usage: get-user.sh <session_id> <username>}"
USERNAME="${2:?Usage: get-user.sh <session_id> <username>}"

curl -sf "$API/user?username=$USERNAME" \
  -H "X-Session-ID: $SID" 2>&1 | python3 -c "
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
    print(f'Category: {d.get(\"category\", \"N/A\")}')
    print(f'Is business: {d.get(\"is_business\", \"N/A\")}')
    print(f'Is verified: {d.get(\"is_verified\", \"N/A\")}')
except Exception as e:
    print(f'Error: {e}')
    print(sys.stdin.read()[:200])
"
