#!/usr/bin/env bash
# Print the view URL for a media asset by storage path.
# Usage: media-url.sh <storage-path>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

PATH_VAL="${1:?Usage: media-url.sh <storage-path>}"
API="${SOCIAL_API_URL:-http://127.0.0.1:8083}"

ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$PATH_VAL'))")
echo "${API}/api/v1/media/view?path=${ENCODED}"
