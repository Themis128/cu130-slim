#!/usr/bin/env bash
# TikTok browser sidecar session helpers.
# Usage: sidecar-session.sh status|ensure
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../../" && pwd)"
cd "$ROOT"
SIDECAR="${TIKTOK_SIDECAR_URL:-http://127.0.0.1:9224}"
ACTION="${1:-status}"
OUT_DIR="${ROOT}/.cursor/tmp-tiktok-pw/out"
NODE_WORK="${ROOT}/.cursor/tmp-tiktok-pw/node_work"
mkdir -p "$OUT_DIR" "$NODE_WORK"

status() {
  curl -sf "$SIDECAR/health" | python3 -m json.tool
  curl -sf "$SIDECAR/session" | python3 -m json.tool || true
}

ensure_playwright_pkg() {
  if [ ! -d "$NODE_WORK/node_modules/playwright" ]; then
    docker run --rm -v "$NODE_WORK:/work" -w /work \
      mcr.microsoft.com/playwright:v1.62.1 \
      bash -lc 'npm init -y >/dev/null && npm i playwright@1.62.1 --no-fund --no-audit'
  fi
  cp -f "$(dirname "$0")/lib/ensure-session.mjs" "$NODE_WORK/ensure-session.mjs"
}

ensure() {
  ensure_playwright_pkg
  set -a
  # shellcheck disable=SC1091
  source <(grep -E '^(TIKTOK_DEV_EMAIL|TIKTOK_DEV_PASSWORD)=' .env | sed 's/\r$//')
  set +a
  if [ -z "${TIKTOK_DEV_EMAIL:-}" ] || [ -z "${TIKTOK_DEV_PASSWORD:-}" ]; then
    echo "Need TIKTOK_DEV_EMAIL and TIKTOK_DEV_PASSWORD in .env" >&2
    exit 1
  fi
  docker run --rm --network host \
    -e TIKTOK_DEV_EMAIL -e TIKTOK_DEV_PASSWORD \
    -e TIKTOK_SIDECAR_URL="$SIDECAR" \
    -e OUT_DIR=/out \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    -v "$NODE_WORK:/work" \
    -v "$OUT_DIR:/out" \
    -w /work \
    mcr.microsoft.com/playwright:v1.62.1 \
    node /work/ensure-session.mjs
  status
}

case "$ACTION" in
  status) status ;;
  ensure) ensure ;;
  *) echo "Usage: $0 status|ensure" >&2; exit 2 ;;
esac
