#!/usr/bin/env bash
# Save Instagram private API credentials.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

USERNAME="${1:-}"
PASSWORD="${2:-}"
if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
  echo "Usage: $(basename "$0") <username> <password>"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/set-secret.sh" INSTAGRAM_USERNAME "$USERNAME" "Instagram private API username"
"$SCRIPT_DIR/set-secret.sh" INSTAGRAM_PASSWORD "$PASSWORD" "Instagram private API password"
