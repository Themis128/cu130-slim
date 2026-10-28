#!/usr/bin/env python3
"""Complete fix for docker-compose.yml to use pre-built images from GHCR.
Usage: fix-compose-for-prebuilt.py"""

import shutil
import sys
import time

import yaml


def log_info(msg: str) -> None:
    print(f"[INFO] {msg}")


COMPOSE_FILE = "docker-compose.yml"
BACKUP_FILE = f"docker-compose.yml.prebuild.{time.strftime('%Y%m%d_%H%M%S')}"

log_info(f"Backing up {COMPOSE_FILE} to {BACKUP_FILE}")
shutil.copy(COMPOSE_FILE, BACKUP_FILE)

with open(COMPOSE_FILE) as f:
    compose = yaml.safe_load(f)

GHCR_NS = "ghcr.io/themis128"
PROJECT_NAME = "cu130-slim"

# Services that should use pre-built images from GHCR
custom_services = {
    "comfyui": f"{GHCR_NS}/{PROJECT_NAME}-comfyui:latest",
    "env-manager-backend": f"{GHCR_NS}/{PROJECT_NAME}-env-manager-backend:latest",
    "env-manager-frontend": f"{GHCR_NS}/{PROJECT_NAME}-env-manager-frontend:latest",
    "social-api": f"{GHCR_NS}/{PROJECT_NAME}-social-api:latest",
    "social-worker": f"{GHCR_NS}/{PROJECT_NAME}-social-worker:latest",
    "social-frontend": f"{GHCR_NS}/{PROJECT_NAME}-social-frontend:latest",
}

# Remove build sections and ensure image is set for custom services
for service_name, image_name in custom_services.items():
    if service_name in compose["services"]:
        compose["services"][service_name]["image"] = image_name
        if "build" in compose["services"][service_name]:
            del compose["services"][service_name]["build"]
            log_info(f"Removed build section for {service_name}")

# Remove duplicate .env volume mount for env-manager-backend
if "env-manager-backend" in compose["services"]:
    volumes = compose["services"]["env-manager-backend"].get("volumes", [])
    seen = set()
    new_volumes = []
    for v in volumes:
        if isinstance(v, str) and "./.env:/app/.env:rw" in v:
            if "./.env:/app/.env:rw" in seen:
                log_info("Removed duplicate .env volume for "
                         "env-manager-backend")
                continue
            seen.add("./.env:/app/.env:rw")
        new_volumes.append(v)
    compose["services"]["env-manager-backend"]["volumes"] = new_volumes

# Remove .env mounts from services that don't need them
services_not_needing_env = ["n8n", "n8n-sandbox", "ollama", "comfyui",
                            "social-postgres", "social-frontend"]
for svc in services_not_needing_env:
    if svc in compose["services"] and "volumes" in compose["services"][svc]:
        volumes = compose["services"][svc]["volumes"]
        new_volumes = [v for v in volumes
                       if not (isinstance(v, str) and
                               "./.env:/app/.env:rw" in v)]
        if len(new_volumes) != len(volumes):
            log_info(f"Removed .env mount from {svc}")
            compose["services"][svc]["volumes"] = new_volumes

# Fix social-frontend: add depends_on social-api if missing
if "social-frontend" in compose["services"]:
    if "depends_on" not in compose["services"]["social-frontend"]:
        compose["services"]["social-frontend"]["depends_on"] = ["social-api"]
        log_info("Added depends_on social-api to social-frontend")

with open(COMPOSE_FILE, "w") as f:
    yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

print("[SUCCESS] docker-compose.yml updated for pre-built images")
