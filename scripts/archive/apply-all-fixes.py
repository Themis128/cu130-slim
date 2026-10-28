#!/usr/bin/env python3
"""Apply all docker-compose.yml fixes"""

import yaml
import sys

with open('docker-compose.yml', 'r') as f:
    compose = yaml.safe_load(f)

# ============================================================
# FIX 1: Add stable-diffusion-nim service (after ollama)
# ============================================================
stable_diffusion_nim = {
    'image': 'nvcr.io/nim/stable-diffusion-3.5-medium:latest',
    'container_name': 'stable-diffusion-nim',
    'ports': ['8000:8000'],
    'environment': [
        'NGC_API_KEY=${NGC_API_KEY}',
        'HF_TOKEN=${HF_TOKEN}',
    ],
    'volumes': [
        './nim-cache:/opt/nim/.cache',
    ],
    'deploy': {
        'resources': {
            'reservations': {
                'devices': [{
                    'driver': 'nvidia',
                    'count': 'all',
                    'capabilities': ['gpu']
                }]
            }
        }
    },
    'healthcheck': {
        'test': ['CMD', 'curl', '-f', 'http://localhost:8000/health'],
        'interval': '30s',
        'timeout': '10s',
        'retries': 5,
        'start_period': '120s'
    },
    'restart': 'unless-stopped'
}

# Insert after ollama
services = compose['services']
new_services = {}
for name, config in services.items():
    new_services[name] = config
    if name == 'ollama':
        new_services['stable-diffusion-nim'] = stable_diffusion_nim
compose['services'] = new_services

print("✅ Added stable-diffusion-nim service")

# ============================================================
# FIX 2: Fix redis command (single line)
# ============================================================
if 'redis' in compose['services']:
    compose['services']['redis']['command'] = 'redis-server --appendonly yes --requirepass ${REDIS_PASSWORD:?REDIS_PASSWORD must be set}'
    print("✅ Fixed redis command format")

# ============================================================
# FIX 3: Add healthchecks to critical services
# ============================================================

# social-postgres
if 'social-postgres' in compose['services']:
    compose['services']['social-postgres']['healthcheck'] = {
        'test': ['CMD-SHELL', 'pg_isready -U ${SOCIAL_POSTGRES_USER} -d ${SOCIAL_POSTGRES_DB}'],
        'interval': '10s',
        'timeout': '5s',
        'retries': 5,
        'start_period': '30s'
    }
    print("✅ Added healthcheck to social-postgres")

# redis
if 'redis' in compose['services']:
    compose['services']['redis']['healthcheck'] = {
        'test': ['CMD', 'redis-cli', 'ping'],
        'interval': '10s',
        'timeout': '5s',
        'retries': 5,
        'start_period': '10s'
    }
    print("✅ Added healthcheck to redis")

# n8n
if 'n8n' in compose['services']:
    compose['services']['n8n']['healthcheck'] = {
        'test': ['CMD', 'curl', '-f', 'http://localhost:5678/healthz'],
        'interval': '30s',
        'timeout': '10s',
        'retries': 3,
        'start_period': '60s'
    }
    print("✅ Added healthcheck to n8n")

# comfyui
if 'comfyui' in compose['services']:
    compose['services']['comfyui']['healthcheck'] = {
        'test': ['CMD', 'curl', '-f', 'http://localhost:8188/health'],
        'interval': '30s',
        'timeout': '10s',
        'retries': 3,
        'start_period': '60s'
    }
    print("✅ Added healthcheck to comfyui")

# ollama
if 'ollama' in compose['services']:
    compose['services']['ollama']['healthcheck'] = {
        'test': ['CMD', 'curl', '-f', 'http://localhost:11434/'],
        'interval': '30s',
        'timeout': '10s',
        'retries': 3,
        'start_period': '60s'
    }
    print("✅ Added healthcheck to ollama")

# chroma
if 'chroma' in compose['services']:
    compose['services']['chroma']['healthcheck'] = {
        'test': ['CMD', 'curl', '-f', 'http://localhost:8000/api/v1/heartbeat'],
        'interval': '30s',
        'timeout': '10s',
        'retries': 3,
        'start_period': '30s'
    }
    print("✅ Added healthcheck to chroma")

# stable-diffusion-nim
if 'stable-diffusion-nim' in compose['services']:
    compose['services']['stable-diffusion-nim']['healthcheck'] = {
        'test': ['CMD', 'curl', '-f', 'http://localhost:8000/health'],
        'interval': '30s',
        'timeout': '10s',
        'retries': 5,
        'start_period': '180s'
    }
    print("✅ Added healthcheck to stable-diffusion-nim")

# ============================================================
# FIX 4: Update depends_on with condition: service_healthy
# ============================================================

# social-api depends on healthy services
if 'social-api' in compose['services']:
    compose['services']['social-api']['depends_on'] = {
        'social-postgres': {'condition': 'service_healthy'},
        'redis': {'condition': 'service_healthy'},
        'n8n': {'condition': 'service_healthy'},
        'comfyui': {'condition': 'service_healthy'},
        'ollama': {'condition': 'service_healthy'},
        'chroma': {'condition': 'service_healthy'},
    }
    print("✅ Updated social-api depends_on with health conditions")

# social-worker depends on healthy services
if 'social-worker' in compose['services']:
    compose['services']['social-worker']['depends_on'] = {
        'social-postgres': {'condition': 'service_healthy'},
        'redis': {'condition': 'service_healthy'},
        'n8n': {'condition': 'service_healthy'},
        'comfyui': {'condition': 'service_healthy'},
        'ollama': {'condition': 'service_healthy'},
    }
    print("✅ Updated social-worker depends_on with health conditions")

# social-frontend depends on healthy social-api
if 'social-frontend' in compose['services']:
    compose['services']['social-frontend']['depends_on'] = {
        'social-api': {'condition': 'service_healthy'},
    }
    print("✅ Updated social-frontend depends_on with health conditions")

# env-manager-frontend depends on healthy env-manager-backend
if 'env-manager-frontend' in compose['services']:
    compose['services']['env-manager-frontend']['depends_on'] = {
        'env-manager-backend': {'condition': 'service_started'},
    }
    print("✅ Updated env-manager-frontend depends_on")

# metabase depends on healthy postgres
if 'metabase' in compose['services']:
    compose['services']['metabase']['depends_on'] = {
        'postgres': {'condition': 'service_healthy'},
    }
    print("✅ Updated metabase depends_on with health conditions")

# ============================================================
# FIX 5: Remove unnecessary .env mounts
# ============================================================
# Services that don't need .env mount
services_not_needing_env = ['chroma', 'portainer', 'redis', 'ollama', 'n8n', 'n8n-sandbox', 'comfyui', 'social-postgres']

for svc in services_not_needing_env:
    if svc in compose['services'] and 'volumes' in compose['services'][svc]:
        volumes = compose['services'][svc]['volumes']
        new_volumes = [v for v in volumes if not (isinstance(v, str) and './.env:/app/.env:rw' in v)]
        if len(new_volumes) != len(volumes):
            compose['services'][svc]['volumes'] = new_volumes
            print(f"✅ Removed .env mount from {svc}")

# Also remove from social-frontend (static frontend doesn't need .env at runtime)
if 'social-frontend' in compose['services'] and 'volumes' in compose['services']['social-frontend']:
    volumes = compose['services']['social-frontend']['volumes']
    new_volumes = [v for v in volumes if not (isinstance(v, str) and './.env:/app/.env:rw' in v)]
    if len(new_volumes) != len(volumes):
        compose['services']['social-frontend']['volumes'] = new_volumes
        print("✅ Removed .env mount from social-frontend")

# ============================================================
# FIX 6: Add resource limits to all services
# ============================================================
resource_limits = {
    'comfyui': {'cpus': '4', 'memory': '8G'},
    'n8n': {'cpus': '2', 'memory': '2G'},
    'n8n-sandbox': {'cpus': '1', 'memory': '1G'},
    'postgres': {'cpus': '1', 'memory': '1G'},
    'metabase': {'cpus': '1', 'memory': '2G'},
    'ollama': {'cpus': '4', 'memory': '8G'},
    'chroma': {'cpus': '1', 'memory': '1G'},
    'portainer': {'cpus': '0.5', 'memory': '512M'},
    'env-manager-backend': {'cpus': '1', 'memory': '512M'},
    'env-manager-frontend': {'cpus': '0.5', 'memory': '256M'},
    'social-postgres': {'cpus': '1', 'memory': '1G'},
    'redis': {'cpus': '0.5', 'memory': '512M'},
    'social-api': {'cpus': '2', 'memory': '2G'},
    'social-worker': {'cpus': '2', 'memory': '2G'},
    'social-frontend': {'cpus': '1', 'memory': '512M'},
    'stable-diffusion-nim': {'cpus': '4', 'memory': '12G'},
}

for svc, limits in resource_limits.items():
    if svc in compose['services']:
        if 'deploy' not in compose['services'][svc]:
            compose['services'][svc]['deploy'] = {}
        if 'resources' not in compose['services'][svc]['deploy']:
            compose['services'][svc]['deploy']['resources'] = {}
        compose['services'][svc]['deploy']['resources']['limits'] = limits
        # Also add reservations (half of limits)
        compose['services'][svc]['deploy']['resources']['reservations'] = {
            'cpus': str(float(limits['cpus']) / 2),
            'memory': str(int(limits['memory'].rstrip('GM')) // 2) + limits['memory'][-1]
        }
        print(f"✅ Added resource limits to {svc}")

# ============================================================
# Write back
# ============================================================
with open('docker-compose.yml', 'w') as f:
    yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

print("\n✅ All fixes applied successfully!")