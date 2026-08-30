#!/usr/bin/env python3
"""
Initialize n8n API key after n8n starts.
Run this after n8n container is up and running.
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, timedelta

N8N_URL = os.getenv("N8N_URL", "http://localhost:5678")
N8N_USER = os.getenv("N8N_USER", "admin@n8n.local")
N8N_PASSWORD = os.getenv("N8N_PASSWORD", "secure_password")
API_KEY_LABEL = os.getenv("API_KEY_LABEL", "social-automation-api-key")
API_KEY_EXPIRY_DAYS = int(os.getenv("API_KEY_EXPIRY_DAYS", "365"))
ENV_FILE = "/home/tbaltzakis/ComfyUI-Docker/cu130-slim/.env"


def wait_for_n8n(timeout=300):
    """Wait for n8n to be ready."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            resp = requests.get(
                f"{N8N_URL}/healthz",
                auth=(N8N_USER, N8N_PASSWORD),
                timeout=5
            )
            if resp.status_code == 200:
                print("n8n is ready!")
                return True
        except requests.RequestException:
            pass
        print("  n8n not ready yet, waiting...")
        time.sleep(5)
    return False


def install_existing_key() -> bool:
    """Check if API key already exists and, if so, write it to .env."""
    try:
        resp = requests.get(
            f"{N8N_URL}/api/v1/user/api-keys",
            auth=(N8N_USER, N8N_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            for key in data.get("data", []):
                if key.get("label") == API_KEY_LABEL:
                    return update_env_file(key.get("key"))
    except Exception:
        print("Error checking existing keys (details hidden for security)")
    return False


def create_and_store_api_key() -> bool:
    """Create a new API key and persist it to .env."""
    expires_at = (datetime.utcnow() + timedelta(days=API_KEY_EXPIRY_DAYS)).isoformat() + "Z"

    payload = {
        "label": API_KEY_LABEL,
        "expiresAt": expires_at,
        "scopes": [
            "workflow:create",
            "workflow:read",
            "workflow:execute",
            "workflow:list",
            "workflow:update",
            "workflow:delete",
            "workflow:activate"
        ]
    }

    try:
        resp = requests.post(
            f"{N8N_URL}/api/v1/user/api-keys",
            auth=(N8N_USER, N8N_PASSWORD),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json=payload,
            timeout=30
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            api_key = None
            # Try different response formats
            if "data" in data and "key" in data["data"]:
                api_key = data["data"]["key"]
            elif "key" in data:
                api_key = data["key"]
            elif "apiKey" in data:
                api_key = data["apiKey"]
            if api_key:
                return update_env_file(api_key)
        return False
    except Exception:
        print("Error creating API key (details hidden for security)")
        return False


def update_env_file(api_key):
    """Update .env file with the new API key."""
    try:
        with open(ENV_FILE, "r") as f:
            lines = f.readlines()

        with open(ENV_FILE, "w") as f:
            for line in lines:
                if line.startswith("# N8N_API_KEY=") or line.startswith("N8N_API_KEY="):
                    f.write(f"N8N_API_KEY={api_key}\n")
                else:
                    f.write(line)
        os.chmod(ENV_FILE, 0o600)
        print("Updated .env file (value hidden for security)")
        return True
    except Exception:
        print("Error updating .env file (details hidden for security)")
        return False


def main():
    print("Initializing n8n API key...")

    if not wait_for_n8n():
        print("ERROR: n8n did not become ready in time")
        sys.exit(1)

    # Check existing
    if install_existing_key():
        print("API key already exists")
        return

    # Create new
    if not create_and_store_api_key():
        print("ERROR: Failed to create API key")
        sys.exit(1)

    print("Successfully created API key (value hidden for security)")

    print("\nNext steps:")
    print("1. Restart social-api and social-worker containers:")
    print("   docker-compose restart social-api social-worker")
    print("2. Or manually add N8N_API_KEY to .env and restart.")


if __name__ == "__main__":
    main()