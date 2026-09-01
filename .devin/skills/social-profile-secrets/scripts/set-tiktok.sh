#!/usr/bin/env bash
# Save TikTok private API signing key.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

API_KEY="${1:-}"
if [ -z "$API_KEY" ]; then
  echo "Usage: $(basename "$0") <api-key>"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/set-secret.sh" TIKTOK_PRIVATE_API_KEY "$API_KEY" "TikTok private API signing key"
