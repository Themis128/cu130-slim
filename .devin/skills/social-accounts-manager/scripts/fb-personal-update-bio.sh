#!/usr/bin/env bash
# Update Facebook personal profile bio via browser sidecar
# Usage: fb-personal-update-bio.sh "Bio text"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

BIO="${1:?Usage: fb-personal-update-bio.sh <bio>}"
SIDECAR="http://localhost:9226"

echo "→ Updating FB personal bio (${#BIO} chars)..."

curl -sf -X POST "$SIDECAR/profile/bio" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "import json; print(json.dumps({'bio': '''$BIO'''}))")" 2>&1 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f'  Result: {d.get(\"status\", d.get(\"error\", \"unknown\"))}')
except:
    print(sys.stdin.read()[:100])
"
