#!/usr/bin/env bash
# List or add TikTok DNS TXT records on cloudless.gr via Cloudflare API.
# Usage:
#   dns-tiktok-txt.sh list
#   dns-tiktok-txt.sh add 'tiktok-domain-verification=....'
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../../" && pwd)"
cd "$ROOT"
ACTION="${1:-list}"
TOKEN_VALUE="${2:-}"

python3 - "$ACTION" "$TOKEN_VALUE" <<'PY'
import json, sys, urllib.request
from pathlib import Path

action = sys.argv[1]
token_value = sys.argv[2] if len(sys.argv) > 2 else ''

env = {}
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

cf_token = env.get('CLOUDFLARE_API_TOKEN') or env.get('CLOUDFLARE_DNS_API_TOKEN')
if not cf_token:
    print(json.dumps({'ok': False, 'error': 'No CLOUDFLARE_API_TOKEN'}))
    sys.exit(1)

def req(url, method='GET', data=None):
    body = None if data is None else json.dumps(data).encode()
    r = urllib.request.Request(url, data=body, method=method, headers={
        'Authorization': f'Bearer {cf_token}',
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(r) as resp:
        return json.load(resp)

z = req('https://api.cloudflare.com/client/v4/zones?name=cloudless.gr')
if not z.get('success') or not z.get('result'):
    print(json.dumps({'ok': False, 'error': 'zone lookup failed', 'detail': z.get('errors')}))
    sys.exit(1)
zone_id = z['result'][0]['id']

if action == 'list':
    data = req(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=TXT&per_page=100')
    rows = []
    for r in data.get('result', []):
        c = r.get('content', '')
        if 'tiktok' not in c.lower():
            continue
        kind = 'domain' if 'tiktok-domain-verification=' in c else (
            'site' if 'tiktok-developers-site-verification=' in c else 'other')
        shown = c if len(c) < 48 else c[:36] + '…' + c[-8:]
        rows.append({'id': r['id'], 'name': r['name'], 'kind': kind, 'content': shown})
    print(json.dumps({'ok': True, 'records': rows}, indent=2))
    sys.exit(0)

if action == 'add':
    content = token_value.strip().strip('"')
    if not content.startswith('tiktok-domain-verification='):
        print(json.dumps({'ok': False, 'error': 'token must start with tiktok-domain-verification='}))
        sys.exit(1)
    # Skip if already present
    data = req(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=TXT&per_page=100')
    for r in data.get('result', []):
        if r.get('content') == content:
            print(json.dumps({'ok': True, 'action': 'exists', 'id': r['id']}))
            sys.exit(0)
    created = req(
        f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records',
        method='POST',
        data={'type': 'TXT', 'name': '@', 'content': content, 'ttl': 120},
    )
    print(json.dumps({
        'ok': bool(created.get('success')),
        'action': 'created',
        'id': (created.get('result') or {}).get('id'),
        'errors': created.get('errors'),
    }, indent=2))
    sys.exit(0 if created.get('success') else 1)

print(json.dumps({'ok': False, 'error': f'unknown action {action}'}))
sys.exit(2)
PY
