#!/usr/bin/env bash
# Compare SocialAuto .env + live sidecar/DNS against official TikTok expectations.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../../" && pwd)"
cd "$ROOT"

EXPECTED_REDIRECT='https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback'
EXPECTED_DOMAIN='cloudless.gr'
EXPECTED_SCOPES='user.info.basic,user.info.profile,user.info.stats,video.list,video.publish,video.upload'

load_env_key() {
  local k="$1"
  grep -E "^${k}=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/\r$//' | sed 's/^"//;s/"$//'
}

echo "=== TikTok config vs official docs ==="
CK="$(load_env_key TIKTOK_CLIENT_KEY)"
CS="$(load_env_key TIKTOK_CLIENT_SECRET)"
RU="$(load_env_key TIKTOK_REDIRECT_URI)"
DE="$(load_env_key TIKTOK_DEV_EMAIL)"
DP="$(load_env_key TIKTOK_DEV_PASSWORD)"

echo "client_key:          $([ -n "$CK" ] && echo "set (${#CK} chars)" || echo MISSING)"
echo "client_secret:       $([ -n "$CS" ] && echo set || echo MISSING)"
echo "redirect_uri:        ${RU:-MISSING}"
echo "dev_email:           ${DE:-MISSING}"
echo "dev_password:        $([ -n "$DP" ] && echo set || echo MISSING)"

ok=0
fail=0
check() {
  local name="$1" cond="$2" detail="$3"
  if [ "$cond" = "1" ]; then
    echo "OK   $name — $detail"
    ok=$((ok+1))
  else
    echo "FAIL $name — $detail"
    fail=$((fail+1))
  fi
}

[ -n "$CK" ] && check client_key 1 present || check client_key 0 missing
[ -n "$CS" ] && check client_secret 1 present || check client_secret 0 missing
[ "$RU" = "$EXPECTED_REDIRECT" ] && check redirect_uri 1 "$RU" || check redirect_uri 0 "want $EXPECTED_REDIRECT got ${RU:-empty}"
[ -n "$DE" ] && check dev_email 1 present || check dev_email 0 missing
[ -n "$DP" ] && check dev_password 1 present || check dev_password 0 missing

echo ""
echo "Expected scopes (OAuth): $EXPECTED_SCOPES"
echo "Expected domain verify:  $EXPECTED_DOMAIN (tiktok-domain-verification TXT)"
echo "Publish pre-audit:       MEDIA_UPLOAD (not DIRECT_POST)"
echo ""

# Sidecar
if curl -sf http://127.0.0.1:9224/health >/tmp/tt-side-health.json 2>/dev/null; then
  python3 -c "import json;d=json.load(open('/tmp/tt-side-health.json'));print('sidecar:', d)"
  has=$(python3 -c "import json;print(json.load(open('/tmp/tt-side-health.json')).get('has_session'))")
  [ "$has" = "True" ] && check sidecar_session 1 has_session=true || check sidecar_session 0 has_session=false
else
  check sidecar 0 unreachable
fi

# DNS kinds
python3 <<'PY' || true
import json, urllib.request
from pathlib import Path
env={}
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
token=env.get('CLOUDFLARE_API_TOKEN') or env.get('CLOUDFLARE_DNS_API_TOKEN')
if not token:
    print('DNS: no Cloudflare token'); raise SystemExit
req=urllib.request.Request('https://api.cloudflare.com/client/v4/zones?name=cloudless.gr',
    headers={'Authorization':f'Bearer {token}'})
z=json.load(urllib.request.urlopen(req))
if not z.get('success'):
    print('DNS: zone lookup failed', z.get('errors')); raise SystemExit
zid=z['result'][0]['id']
req=urllib.request.Request(f'https://api.cloudflare.com/client/v4/zones/{zid}/dns_records?type=TXT&per_page=100',
    headers={'Authorization':f'Bearer {token}'})
data=json.load(urllib.request.urlopen(req))
kinds=[]
for r in data.get('result',[]):
    c=r.get('content','')
    if 'tiktok-domain-verification=' in c: kinds.append('domain')
    elif 'tiktok-developers-site-verification=' in c: kinds.append('site')
    elif 'tiktok' in c.lower(): kinds.append('other')
print('DNS tiktok TXT kinds:', kinds or ['none'])
print('domain_verified_dns:', 'domain' in kinds)
print('site_verification_only:', kinds==['site'] or (kinds==['site'] ))
PY

# API smoke
if curl -sf http://127.0.0.1:8083/health >/dev/null 2>&1; then
  check social_api 1 healthy
else
  check social_api 0 down
fi

echo ""
echo "Summary: $ok ok, $fail fail"
[ "$fail" -eq 0 ]
