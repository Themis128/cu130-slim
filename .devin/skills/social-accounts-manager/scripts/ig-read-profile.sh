#!/usr/bin/env bash
# Read Instagram profile via instagrapi sidecar
# Usage: ig-read-profile.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API="http://localhost:8011"

# Get session ID
SID="${IG_SESSION_ID:-}"
if [ -z "$SID" ] && [ -f /tmp/ig-sidecar-session.txt ]; then
  SID=$(cat /tmp/ig-sidecar-session.txt)
fi

echo "=== Instagram Profile ==="

if [ -n "$SID" ]; then
  curl -sf "$API/account" \
    -H "X-Session-ID: $SID" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'  Username: {d.get(\"username\")}')
    print(f'  Full name: {d.get(\"full_name\")}')
    print(f'  Biography: {d.get(\"biography\")}')
    print(f'  External URL: {d.get(\"external_url\")}')
    print(f'  Followers: {d.get(\"follower_count\")}')
    print(f'  Following: {d.get(\"following_count\")}')
    print(f'  Media: {d.get(\"media_count\")}')
    print(f'  Is business: {d.get(\"is_business\")}')
except:
    print('  (error reading profile)')
"
else
  echo "  (not logged in - run ig-login.sh first)"
fi
