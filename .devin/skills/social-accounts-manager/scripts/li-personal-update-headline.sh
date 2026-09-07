#!/usr/bin/env bash
# Update LinkedIn personal headline via browser sidecar
# Usage: li-personal-update-headline.sh "Headline text"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

HEADLINE="${1:?Usage: li-personal-update-headline.sh <headline>}"
SIDECAR="http://localhost:9225"

echo "→ Updating LinkedIn personal headline (${#HEADLINE} chars)..."

# Use the sidecar's built-in headline update endpoint
curl -sf -X POST "$SIDECAR/profile/headline" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json; print(json.dumps({'headline': '''$HEADLINE'''}))")" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'  Result: {d.get(\"status\", d.get(\"error\", \"unknown\"))}')
except:
    print(sys.stdin.read()[:100])
"
