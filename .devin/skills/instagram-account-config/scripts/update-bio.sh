#!/usr/bin/env bash
# Update Instagram bio via browser-novnc bridge
# Usage: ./update-bio.sh "Your bio text here"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

if [ $# -lt 1 ]; then
    echo "Usage: $0 \"Your bio text\""
    echo "Example: $0 \"🚀 Founder @ Cloudless\\n☁️ Cloud Architect\""
    exit 1
fi

BIO="$1"

echo "Updating Instagram bio via browser bridge..."
curl -s -X PATCH http://localhost:9223/profile/instagram \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "import json; print(json.dumps({'biography': '''$BIO'''}))")" | \
    python3 -m json.tool

echo ""
echo "Verifying..."
sleep 5
curl -s http://localhost:9223/profile/instagram | python3 -m json.tool
