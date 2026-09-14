#!/usr/bin/env bash
# Login to TikTok developer console and dump Cloudless app state (JSON + screenshots).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../../" && pwd)"
cd "$ROOT"
OUT_DIR="${ROOT}/.cursor/tmp-tiktok-pw/out"
NODE_WORK="${ROOT}/.cursor/tmp-tiktok-pw/node_work"
mkdir -p "$OUT_DIR" "$NODE_WORK"

if [ ! -d "$NODE_WORK/node_modules/playwright" ]; then
  docker run --rm -v "$NODE_WORK:/work" -w /work \
    mcr.microsoft.com/playwright:v1.62.1 \
    bash -lc 'npm init -y >/dev/null && npm i playwright@1.62.1 --no-fund --no-audit'
fi
cp -f "$(dirname "$0")/lib/console-inspect.mjs" "$NODE_WORK/console-inspect.mjs"

set -a
# shellcheck disable=SC1091
source <(grep -E '^(TIKTOK_DEV_EMAIL|TIKTOK_DEV_PASSWORD)=' .env | sed 's/\r$//')
set +a

docker run --rm --network host \
  -e TIKTOK_DEV_EMAIL -e TIKTOK_DEV_PASSWORD \
  -e OUT_DIR=/out -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  -v "$NODE_WORK:/work" -v "$OUT_DIR:/out" -w /work \
  mcr.microsoft.com/playwright:v1.62.1 \
  node /work/console-inspect.mjs
