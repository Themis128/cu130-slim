#!/usr/bin/env bash
# One-time Google Photos OAuth consent → appends GOOGLE_PHOTOS_REFRESH_TOKEN to .env
# Usage: GOOGLE_PHOTOS_CLIENT_ID=... GOOGLE_PHOTOS_CLIENT_SECRET=... ./login.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

: "${GOOGLE_PHOTOS_CLIENT_ID:?set it (Google Cloud OAuth client id)}"
: "${GOOGLE_PHOTOS_CLIENT_SECRET:?set it (Google Cloud OAuth client secret)}"
export GOOGLE_PHOTOS_CLIENT_ID GOOGLE_PHOTOS_CLIENT_SECRET

echo "Opening Google consent in your browser — approve, then the refresh token prints below."
npx -y @thenavidm/google-photos-mcp-cli google-photos-mcp login

echo
echo "Paste the printed GOOGLE_PHOTOS_REFRESH_TOKEN value into .env (gitignored):"
echo "  GOOGLE_PHOTOS_REFRESH_TOKEN=..."
