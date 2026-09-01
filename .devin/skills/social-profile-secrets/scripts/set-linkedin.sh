#!/usr/bin/env bash
# Save LinkedIn browser automation credentials.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

USERNAME="${1:-}"
PASSWORD="${2:-}"
if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
  echo "Usage: $(basename "$0") <email/phone> <password>"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/set-secret.sh" LINKEDIN_USERNAME "$USERNAME" "LinkedIn browser automation username"
"$SCRIPT_DIR/set-secret.sh" LINKEDIN_PASSWORD "$PASSWORD" "LinkedIn browser automation password"
