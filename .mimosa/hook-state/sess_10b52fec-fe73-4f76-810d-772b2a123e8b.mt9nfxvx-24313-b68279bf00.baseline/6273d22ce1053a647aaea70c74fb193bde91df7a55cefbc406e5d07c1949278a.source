#!/bin/bash
# Final fixes for docker-compose.yml

set -euo pipefail

COMPOSE_FILE="docker-compose.yml"

python3 << 'PYEOF'
import yaml

with open('docker-compose.yml', 'r') as f:
    compose = yaml.safe_load(f)

# Fix 1: Update COMFYUI_URL in social-api and social-worker to use port 8188
for svc in ['social-api', 'social-worker']:
    if svc in compose['services'] and 'environment' in compose['services'][svc]:
        env = compose['services'][svc]['environment']
        new_env = []
        for e in env:
            if e.startswith('COMFYUI_URL='):
                new_env.append('COMFYUI_URL=http://comfyui:8188')
                print(f"[INFO] Updated COMFYUI_URL for {svc}")
            else:
                new_env.append(e)
        compose['services'][svc]['environment'] = new_env

# Fix 2: Remove source code volume mounts from social-api and social-worker (production)
# These override the pre-built image code
for svc in ['social-api', 'social-worker']:
    if svc in compose['services'] and 'volumes' in compose['services'][svc]:
        volumes = compose['services'][svc]['volumes']
        new_volumes = [v for v in volumes if not (isinstance(v, str) and './social-automation/backend/app:/app/app' in v)]
        if len(new_volumes) != len(volumes):
            print(f"[INFO] Removed source code mount from {svc}")
            compose['services'][svc]['volumes'] = new_volumes

# Fix 3: Remove .env mount from social-frontend if present
if 'social-frontend' in compose['services'] and 'volumes' in compose['services']['social-frontend']:
    volumes = compose['services']['social-frontend']['volumes']
    new_volumes = [v for v in volumes if not (isinstance(v, str) and './.env:/app/.env:rw' in v)]
    if len(new_volumes) != len(volumes):
        print("[INFO] Removed .env mount from social-frontend")
        compose['services']['social-frontend']['volumes'] = new_volumes

# Fix 4: Ensure redis command is on single line
if 'redis' in compose['services'] and 'command' in compose['services']['redis']:
    compose['services']['redis']['command'] = 'redis-server --appendonly yes --requirepass ${REDIS_PASSWORD:?REDIS_PASSWORD must be set}'
    print("[INFO] Fixed redis command format")

with open('docker-compose.yml', 'w') as f:
    yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

print("Done")
PYEOF

echo "Final fixes applied"