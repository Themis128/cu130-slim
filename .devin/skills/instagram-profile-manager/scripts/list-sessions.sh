#!/usr/bin/env bash
# List saved Instagram sessions in the sidecar's db.json
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

echo "Saved Instagram sessions:"
docker compose exec -T instagram-private-api python3 -c "
import json
with open('/data/db.json') as f:
    d = json.load(f)
for key, val in d.items():
    if isinstance(val, dict):
        # Newer format: key is session name, val has sessionid
        if 'sessionid' in val:
            sid = val['sessionid']
            pk = sid.split(':')[0] if ':' in sid else 'N/A'
            print(f'  Key: {key} | PK: {pk} | SessionID: {sid[:40]}...')
        else:
            # Older format: numbered keys with dict containing sessionid
            for k2, v2 in val.items():
                if isinstance(v2, dict) and 'sessionid' in v2:
                    sid = v2['sessionid']
                    pk = sid.split(':')[0] if ':' in sid else 'N/A'
                    print(f'  Key: {key}.{k2} | PK: {pk} | SessionID: {sid[:40]}...')
    elif isinstance(val, list):
        for i, item in enumerate(val):
            if isinstance(item, dict) and 'sessionid' in item:
                sid = item['sessionid']
                pk = sid.split(':')[0] if ':' in sid else 'N/A'
                print(f'  Key: {key}[{i}] | PK: {pk} | SessionID: {sid[:40]}...')
" 2>/dev/null
