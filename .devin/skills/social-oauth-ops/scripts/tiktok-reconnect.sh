#!/usr/bin/env bash
# Reconnect the TikTok SocialAuto account through OAuth using the stored
# tiktok.com web session. Needed to grant NEW scopes (e.g. user.info.stats)
# — token refresh can only renew, never expand, granted scopes.
#
# Usage:
#   .devin/skills/social-oauth-ops/scripts/tiktok-reconnect.sh [--dry-run]
#
# Prereqs: social-api up, SOCIAL_ADMIN_* in .env, tiktok_web_cookies stored
# on the account (QR login — see tiktok-console-ops skill), Playwright image.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../" && pwd)"
cd "$ROOT"

PW_WORK="$ROOT/.cursor/tmp-tiktok-pw/node_work"
PW_OUT="$ROOT/.cursor/tmp-tiktok-pw/out"
mkdir -p "$PW_WORK" "$PW_OUT"

DRY_RUN=""
[ "${1:-}" = "--dry-run" ] && DRY_RUN="1"

# 1. Export the stored tiktok.com session cookies from the DB
docker exec -i social-api python - <<'PYEOF'
import asyncio, json
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from sqlalchemy import select

async def m():
    async with async_session_maker() as s:
        acc = (await s.execute(select(SocialAccount).where(
            SocialAccount.platform == "tiktok"))).scalars().first()
        cookies = (acc.meta_data or {}).get("tiktok_web_cookies") or {}
        if not cookies:
            raise SystemExit("no tiktok_web_cookies on the account — "
                             "do the QR login first (tiktok-console-ops)")
        out = [{"name": k, "value": v, "domain": ".tiktok.com",
                "path": "/", "secure": True} for k, v in cookies.items()]
        open("/tmp/tt_cookies.json", "w").write(json.dumps(out))
        print(f"wrote {len(out)} cookies")

asyncio.run(m())
PYEOF
docker cp social-api:/tmp/tt_cookies.json "$PW_WORK/tt_cookies.json"

# 2. Get a fresh authorize URL (embeds team_id + PKCE verifier in state)
set -a
# shellcheck disable=SC1091
source <(grep -E '^(SOCIAL_ADMIN_EMAIL|SOCIAL_ADMIN_PASSWORD)=' .env | sed 's/\r$//')
set +a
TOKEN="$(curl -sf -X POST http://127.0.0.1:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=$SOCIAL_ADMIN_EMAIL" \
  --data-urlencode "password=$SOCIAL_ADMIN_PASSWORD" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')"
AUTH_URL="$(curl -sf -X POST http://127.0.0.1:8083/api/v1/accounts/connect \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"platform":"tiktok"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["authorization_url"])')"
echo "authorize URL obtained"

# 3. Drive consent headlessly
cp -f "$(dirname "$0")/tiktok-oauth.mjs" "$PW_WORK/tiktok-oauth.mjs"
docker run --rm --network host \
  -e TT_AUTH_URL="$AUTH_URL" -e OUT_DIR=/out \
  ${DRY_RUN:+-e TT_DRY_RUN=1} \
  -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  -v "$PW_WORK:/work" -v "$PW_OUT:/out" -w /work \
  mcr.microsoft.com/playwright:v1.62.1 \
  node /work/tiktok-oauth.mjs

# 4. Report granted scopes
docker exec -i social-api python - <<'PYEOF'
import asyncio
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from sqlalchemy import select

async def m():
    async with async_session_maker() as s:
        acc = (await s.execute(select(SocialAccount).where(
            SocialAccount.platform == "tiktok"))).scalars().first()
        print("granted scopes:", ", ".join(acc.scopes or []))

asyncio.run(m())
PYEOF
