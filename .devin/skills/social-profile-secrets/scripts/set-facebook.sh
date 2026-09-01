#!/usr/bin/env bash
# Save Facebook browser automation credentials.
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
"$SCRIPT_DIR/set-secret.sh" FACEBOOK_USERNAME "$USERNAME" "Facebook browser automation username"
"$SCRIPT_DIR/set-secret.sh" FACEBOOK_PASSWORD "$PASSWORD" "Facebook browser automation password"
