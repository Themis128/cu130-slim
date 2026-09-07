#!/usr/bin/env bash
# Update Facebook personal profile website via browser sidecar
# Usage: fb-personal-update-website.sh "https://cloudless.gr"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

URL="${1:?Usage: fb-personal-update-website.sh <url>}"
SIDECAR="http://localhost:9226"

echo "→ Updating FB personal website to $URL..."

curl -sf -X POST "$SIDECAR/profile/website" \
  -H "Content-Type: application/json" \
  -d "{\"website\":\"$URL\"}" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'  Result: {d.get(\"status\", d.get(\"error\", \"unknown\"))}')
except:
    print(sys.stdin.read()[:100])
"
