#!/usr/bin/env bash
# Verify the Threads app configuration and tester state.
set -euo pipefail

cd /home/tbaltzakis/cu130-slim

APP_ID="1236823015256887"

echo "=== Threads .env configuration ==="
grep -E "^THREADS_(CLIENT_ID|CLIENT_SECRET|REDIRECT_URI)" .env | sed -E 's/(SECRET)=.*/\1=****/'

echo ""
echo "=== Threads app validation ==="
docker compose exec -T social-api python -c "
import httpx, os
client_id = os.environ.get('THREADS_CLIENT_ID', '$APP_ID')
secret = os.environ.get('THREADS_CLIENT_SECRET', '')
r = httpx.get(
    'https://graph.facebook.com/oauth/access_token',
    params={'client_id': client_id, 'client_secret': secret, 'grant_type': 'client_credentials'},
    timeout=30,
)
print(f'App token request: {r.status_code}')
if r.status_code == 200:
    token = r.json().get('access_token', '')
    r2 = httpx.get(f'https://graph.facebook.com/v21.0/{client_id}/permissions', params={'access_token': token}, timeout=30)
    print(f'Permissions: {r2.status_code}')
    data = r2.json()
    if 'data' in data:
        for perm in data['data']:
            print(f'  {perm.get(\"permission\", \"?\")}: {perm.get(\"status\", \"?\")}')
    else:
        print(f'  {data}')
else:
    print(f'  {r.json()}')
" 2>&1

echo ""
echo "=== How to add a Threads tester ==="
echo "1. Go to https://developers.facebook.com/apps/1936126137016578/roles/roles/"
echo "2. Click 'Add People' -> 'Threads Tester'"
echo "3. Enter the Instagram/Threads username (e.g. cloudless_gr or cloudless.gr)"
echo "4. The user must accept the invite in Threads: Settings -> Account -> Website permissions"
echo ""
echo "=== Then run ==="
echo "  bash .devin/skills/threads-ops/scripts/switch-threads-account.sh cloudless.gr"
echo "  bash .devin/skills/threads-ops/scripts/connect-threads.sh --open"
