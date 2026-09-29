#!/usr/bin/env bash
# Fill/submit the Content Posting API (Direct Post) audit application.
# Default: fills through the Review step without submitting (dry run).
#   cp-audit-application.sh           — prepare only, screenshots to out/
#   cp-audit-application.sh --submit  — tick declarations + submit
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../../" && pwd)"
cd "$ROOT"
PW_WORK="$ROOT/.cursor/tmp-tiktok-pw/node_work"
PW_OUT="$ROOT/.cursor/tmp-tiktok-pw/out"
mkdir -p "$PW_WORK" "$PW_OUT"

if [ ! -d "$PW_WORK/node_modules/playwright" ]; then
  docker run --rm -v "$PW_WORK:/work" -w /work \
    mcr.microsoft.com/playwright:v1.62.1 \
    bash -lc 'npm init -y >/dev/null && npm i playwright@1.62.1 --no-fund --no-audit'
fi
cp -f "$(dirname "$0")/cp-audit-application.mjs" "$PW_WORK/"

# Demo MP4 — must show OAuth → compose → post UX (required by step 3)
DEMO="${TT_AUDIT_VIDEO_HOST:-$ROOT/docs/tiktok-demo/videos/tiktok-demo.mp4}"
cp -f "$DEMO" "$PW_WORK/tiktok-demo.mp4"

set -a
# shellcheck disable=SC1091
source <(grep -E '^(TIKTOK_DEV_EMAIL|TIKTOK_DEV_PASSWORD)=' .env | sed 's/\r$//')
set +a

docker run --rm --network host \
  -e TIKTOK_DEV_EMAIL -e TIKTOK_DEV_PASSWORD \
  -e OUT_DIR=/out -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  -v "$PW_WORK:/work" -v "$PW_OUT:/out" -w /work \
  mcr.microsoft.com/playwright:v1.62.1 \
  node /work/cp-audit-application.mjs "${@:---no-submit-flag}"
