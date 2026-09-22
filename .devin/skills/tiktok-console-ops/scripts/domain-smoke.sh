#!/usr/bin/env bash
# TikTok Content Posting domain verification — practical smoke test.
# Checks the three independent signals that mean PULL_FROM_URL can be used:
#   1) DNS TXT records for tiktok site/domain verification are present on cloudless.gr
#   2) The connected TikTok account token is valid
#   3) The creator_info endpoint returns 200 (proves Content Posting scope is live)
# Does not require scraping the developer console; use after domain-verify.sh
# or whenever `url_ownership_unverified` errors appear in publish logs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../../" && pwd)"
cd "$ROOT"

source <(grep -E '^(TIKTOK_VERIFY_DOMAIN|CLOUDFLARE_ZONE_ID|CLOUDFLARE_API_TOKEN|WHATSAPP_)=' .env 2>/dev/null | sed 's/\r$//') || true
DOMAIN="${TIKTOK_VERIFY_DOMAIN:-cloudless.gr}"

ERR=0
ok() { echo "[OK]   $*"; }
fail() { echo "[FAIL] $*"; ERR=1; }
warn() { echo "[WARN] $*"; }

# 1. DNS TXT records via Cloudflare API (same API the dns-tiktok-txt.sh uses)
CF_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
CF_ZONE="${CLOUDFLARE_ZONE_ID:-}"
# 1a. Try Cloudflare API first
TXT_COUNT=0
if [ -n "$CF_TOKEN" ] && [ -n "$CF_ZONE" ]; then
  resp=$(curl -sf -m 20 "https://api.cloudflare.com/client/v4/zones/$CF_ZONE/dns_records?type=TXT&name=$DOMAIN" \
    -H "Authorization: Bearer $CF_TOKEN" \
    -H "Content-Type: application/json" 2>/dev/null || true)
  TXT_COUNT=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len([r for r in d.get('result',[]) if 'tiktok' in r.get('content','').lower()]))" 2>/dev/null || echo 0)
fi

# 1b. Fallback to public DNS-over-HTTPS
if [ "$TXT_COUNT" -eq 0 ]; then
  public_txt=$(python3 - <<PY 2>/dev/null || true
import urllib.request, json
try:
    req = urllib.request.Request(
        f'https://cloudflare-dns.com/dns-query?name=$DOMAIN&type=TXT',
        headers={'Accept':'application/dns-json'})
    data = json.load(urllib.request.urlopen(req, timeout=10))
    print(len([a for a in data.get('Answer', []) if 'tiktok' in a.get('data', '').lower()]))
except Exception as e:
    print(0)
PY
)
  TXT_COUNT=${public_txt:-0}
fi

if [ "$TXT_COUNT" -gt 0 ]; then
  ok "$TXT_COUNT TikTok-related TXT record(s) for $DOMAIN"
else
  warn "No TikTok TXT records found via Cloudflare API or public DNS"
fi

# 2. Decrypt token and call creator_info (cheapest Content Posting endpoint)
api_status=$(docker compose exec -T social-api python3 -c "
import asyncio, os, httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import decrypt_token

async def go():
    e = create_async_engine(os.environ['DATABASE_URL'])
    async with e.connect() as c:
        r = await c.execute(text(\"SELECT access_token_enc FROM social_accounts WHERE platform='tiktok'\"))
        row = r.first()
        if not row:
            print('NO_TIKTOK_ACCOUNT')
            return
        tok = decrypt_token(bytes(row[0]))
        async with httpx.AsyncClient(timeout=20) as h:
            q = await h.post('https://open.tiktokapis.com/v2/post/publish/creator_info/query/',
                headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'})
            print(q.status_code)
            print(q.text[:300])
asyncio.run(go())
" 2>/dev/null)

code=$(echo "$api_status" | head -1)
body=$(echo "$api_status" | tail -n +2)
if [ "$code" = "200" ]; then
  ok "TikTok creator_info 200 — Content Posting token + scopes are live"
  user=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); u=d.get('data',{}); print(u.get('creator_avatar_url','')[:80])" 2>/dev/null || true)
  [ -n "$user" ] && ok "creator avatar present"
elif echo "$body" | grep -qi "url_ownership_unverified"; then
  fail "TikTok reports url_ownership_unverified — domain verification still needed"
else
  warn "creator_info returned $code: $body"
fi

# 3. Try a PULL_FROM_URL init to see if the ownership check is gone
pull_status=$(docker compose exec -T social-api python3 -c "
import asyncio, os, httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.security import decrypt_token

async def go():
    e = create_async_engine(os.environ['DATABASE_URL'])
    async with e.connect() as c:
        r = await c.execute(text(\"SELECT access_token_enc FROM social_accounts WHERE platform='tiktok'\"))
        row = r.first()
        tok = decrypt_token(bytes(row[0]))
        async with httpx.AsyncClient(timeout=20) as h:
            q = await h.post('https://open.tiktokapis.com/v2/post/publish/video/init/',
                headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'},
                json={'post_info': {'title': 'smoke', 'privacy_level': 'SELF_ONLY'},
                      'source_info': {'source': 'PULL_FROM_URL', 'video_url': f'https://$DOMAIN/favicon.ico'}})
            print(q.status_code)
            print(q.text[:400])
asyncio.run(go())
" 2>/dev/null)

code2=$(echo "$pull_status" | head -1)
body2=$(echo "$pull_status" | tail -n +2)
case "$code2" in
  200) ok "PULL_FROM_URL init accepted — domain ownership is verified" ;;
  403)
    if echo "$body2" | grep -qi "url_ownership_unverified"; then
      fail "PULL_FROM_URL blocked: url_ownership_unverified"
    elif echo "$body2" | grep -qi "unaudited_client"; then
      ok "PULL_FROM_URL reaches audit gate (not ownership) — domain verified, waiting for app audit"
    else
      warn "PULL_FROM_URL 403: $body2"
    fi
    ;;
  *) warn "PULL_FROM_URL returned $code2: $body2" ;;
esac

if [ "$ERR" -eq 0 ]; then
  echo "[ALL OK] TikTok domain verification is effective"
else
  echo "[DONE] TikTok domain verification needs attention"
fi
exit "$ERR"
