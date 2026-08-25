#!/bin/bash
# Complete fix for docker-compose.yml to use pre-built images from Docker Hub

set -euo pipefail

COMPOSE_FILE="docker-compose.yml"
BACKUP_FILE="docker-compose.yml.prebuild.$(date +%Y%m%d_%H%M%S)"

log_info() { echo -e "\033[0;34m[INFO]\033[0m $*"; }
log_success() { echo -e "\033[0;32m[SUCCESS]\033[0m $*"; }

log_info "Backing up ${COMPOSE_FILE} to ${BACKUP_FILE}"
cp "${COMPOSE_FILE}" "${BACKUP_FILE}"

# Use Python for more reliable YAML manipulation
python3 << 'PYEOF'
import yaml
import sys

def log_info(msg):
    print(f"[INFO] {msg}")

with open('docker-compose.yml', 'r') as f:
    compose = yaml.safe_load(f)

DOCKERHUB_USER = "baltzakist"
PROJECT_NAME = "cu130-slim"

# Services that should use pre-built images from Docker Hub
custom_services = {
    'comfyui': f'{DOCKERHUB_USER}/{PROJECT_NAME}-comfyui:latest',
    'env-manager-backend': f'{DOCKERHUB_USER}/{PROJECT_NAME}-env-manager-backend:latest',
    'env-manager-frontend': f'{DOCKERHUB_USER}/{PROJECT_NAME}-env-manager-frontend:latest',
    'social-api': f'{DOCKERHUB_USER}/{PROJECT_NAME}-social-api:latest',
    'social-worker': f'{DOCKERHUB_USER}/{PROJECT_NAME}-social-worker:latest',
    'social-frontend': f'{DOCKERHUB_USER}/{PROJECT_NAME}-social-frontend:latest',
}

# Remove build sections and ensure image is set for custom services
for service_name, image_name in custom_services.items():
    if service_name in compose['services']:
        compose['services'][service_name]['image'] = image_name
        if 'build' in compose['services'][service_name]:
            del compose['services'][service_name]['build']
            log_info(f"Removed build section for {service_name}")

# Remove duplicate .env volume mount for env-manager-backend
if 'env-manager-backend' in compose['services']:
    volumes = compose['services']['env-manager-backend'].get('volumes', [])
    seen = set()
    new_volumes = []
    for v in volumes:
        if isinstance(v, str) and './.env:/app/.env:rw' in v:
            if './.env:/app/.env:rw' in seen:
                log_info("Removed duplicate .env volume for env-manager-backend")
                continue
            seen.add('./.env:/app/.env:rw')
        new_volumes.append(v)
    compose['services']['env-manager-backend']['volumes'] = new_volumes

# Remove .env mounts from services that don't need them
services_not_needing_env = ['n8n', 'n8n-sandbox', 'ollama', 'comfyui', 'social-postgres', 'social-frontend']
for svc in services_not_needing_env:
    if svc in compose['services'] and 'volumes' in compose['services'][svc]:
        volumes = compose['services'][svc]['volumes']
        new_volumes = [v for v in volumes if not (isinstance(v, str) and './.env:/app/.env:rw' in v)]
        if len(new_volumes) != len(volumes):
            log_info(f"Removed .env mount from {svc}")
            compose['services'][svc]['volumes'] = new_volumes

# Fix social-frontend: add depends_on social-api if missing
if 'social-frontend' in compose['services']:
    if 'depends_on' not in compose['services']['social-frontend']:
        compose['services']['social-frontend']['depends_on'] = ['social-api']
        log_info("Added depends_on social-api to social-frontend")

# Write back
with open('docker-compose.yml', 'w') as f:
    yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

print("Done")
PYEOF

log_success "All fixes applied. Backup saved as ${BACKUP_FILE}"