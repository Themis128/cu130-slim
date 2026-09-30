#!/usr/bin/env python3
"""List saved Instagram sessions in the sidecar's db.json."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

PY = """
import json
with open('/data/db.json') as f:
    d = json.load(f)
for key, val in d.items():
    if isinstance(val, dict):
        if 'sessionid' in val:
            sid = val['sessionid']
            pk = sid.split(':')[0] if ':' in sid else 'N/A'
            print(f'  Key: {key} | PK: {pk} | SessionID: {sid[:40]}...')
        else:
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
"""

print("Saved Instagram sessions:")
subprocess.run(
    ["docker", "compose", "exec", "-T", "instagram-private-api",
     "python3", "-c", PY], cwd=repo_root())
