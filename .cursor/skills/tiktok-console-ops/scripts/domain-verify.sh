#!/usr/bin/env bash
# Domain verification flow (official Content Posting media-transfer guide):
#   1) Playwright: capture/add domain token for cloudless.gr
#   2) Cloudflare: add TXT tiktok-domain-verification=...
#   3) Playwright: click Verify (CLICK_VERIFY=1)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../../" && pwd)"
cd "$ROOT"
OUT_DIR="${ROOT}/.cursor/tmp-tiktok-pw/out"
NODE_WORK="${ROOT}/.cursor/tmp-tiktok-pw/node_work"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT_DIR" "$NODE_WORK"

if [ ! -d "$NODE_WORK/node_modules/playwright" ]; then
  docker run --rm -v "$NODE_WORK:/work" -w /work \
    mcr.microsoft.com/playwright:v1.62.1 \
    bash -lc 'npm init -y >/dev/null && npm i playwright@1.62.1 --no-fund --no-audit'
fi
cp -f "$SCRIPTS/lib/domain-verify.mjs" "$NODE_WORK/domain-verify.mjs"

set -a
# shellcheck disable=SC1091
source <(grep -E '^(TIKTOK_DEV_EMAIL|TIKTOK_DEV_PASSWORD)=' .env | sed 's/\r$//')
set +a

echo "== Pass 1: capture domain token =="
docker run --rm --network host \
  -e TIKTOK_DEV_EMAIL -e TIKTOK_DEV_PASSWORD \
  -e OUT_DIR=/out -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  -e CLICK_VERIFY=0 \
  -v "$NODE_WORK:/work" -v "$OUT_DIR:/out" -w /work \
  mcr.microsoft.com/playwright:v1.62.1 \
  node /work/domain-verify.mjs

TOKEN_FILE="$OUT_DIR/domain-verification-token.json"
if [ ! -f "$TOKEN_FILE" ]; then
  echo "No token file written" >&2
  exit 1
fi

TOKEN=$(python3 -c "import json;print(json.load(open('$TOKEN_FILE')).get('token') or '')")
if [ -z "$TOKEN" ]; then
  echo "No tiktok-domain-verification token found — open console manually or fix app URL properties UI" >&2
  python3 -c "import json;print(json.dumps(json.load(open('$TOKEN_FILE')),indent=2)[:2000])"
  exit 2
fi

echo "== Pass 2: add Cloudflare TXT =="
"$SCRIPTS/dns-tiktok-txt.sh" add "$TOKEN"

echo "== Pass 3: click Verify =="
docker run --rm --network host \
  -e TIKTOK_DEV_EMAIL -e TIKTOK_DEV_PASSWORD \
  -e OUT_DIR=/out -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  -e CLICK_VERIFY=1 \
  -v "$NODE_WORK:/work" -v "$OUT_DIR:/out" -w /work \
  mcr.microsoft.com/playwright:v1.62.1 \
  node /work/domain-verify.mjs

echo "== DNS list =="
"$SCRIPTS/dns-tiktok-txt.sh" list
echo "Done. Review $OUT_DIR/domain-verify-result.json if present."
