#!/usr/bin/env bash
# Get the current Instagram account profile via aiograpi-rest.
# Usage: get-profile.sh <session_id>
set -euo pipefail

SIDECAR_URL="${INSTAGRAM_PRIVATE_API_URL:-http://localhost:8011}"

SESSION_ID="${1:?Usage: get-profile.sh <session_id>}"

echo "Fetching Instagram profile..."

curl -sf "${SIDECAR_URL}/account" \
  -H "X-Session-ID: ${SESSION_ID}" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print(f"Username: {d.get('username','?')}")
print(f"Full name: {d.get('full_name','?')}")
print(f"Biography: {d.get('biography','?')}")
print(f"External URL: {d.get('external_url','?')}")
print(f"Followers: {d.get('follower_count','?')}")
print(f"Following: {d.get('following_count','?')}")
print(f"Posts: {d.get('media_count','?')}")
print(f"Is private: {d.get('is_private','?')}")
print(f"Is verified: {d.get('is_verified','?')}")
' 2>/dev/null || echo "✗ Failed to fetch profile"
