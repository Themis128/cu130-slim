#!/usr/bin/env python3
"""Build, tag, push all custom Docker images to GitHub Container Registry
(GHCR). Updates docker-compose.yml to use the chosen tag.

GHCR uses the built-in GITHUB_TOKEN / gh CLI for auth. Make packages public
in the GitHub UI after first push so anonymous pulls (Trivy, compose) work.

Usage: build-tag-push-all.py   (env: GITHUB_OWNER, TAG, GITHUB_TOKEN)"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

GHCR_REGISTRY = "ghcr.io"
PROJECT_NAME = "cu130-slim"
TAG = os.environ.get("TAG", "latest")


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}")


# Resolve GHCR owner
OWNER = os.environ.get("GITHUB_OWNER", "")
if not OWNER and shutil.which("gh"):
    r = subprocess.run(["gh", "repo", "view", "--json", "owner",
                        "-q", ".owner.login"], capture_output=True, text=True)
    if r.returncode == 0:
        OWNER = r.stdout.strip().lower()
OWNER = OWNER or "themis128"


def check_ghcr_login() -> None:
    log("INFO", "Checking GitHub Container Registry authentication...")
    if shutil.which("gh") and subprocess.run(
            ["gh", "auth", "status"], capture_output=True).returncode == 0:
        log("SUCCESS", "Authenticated with gh CLI")
        return
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if gh_token:
        log("INFO", "Logging into ghcr.io with GITHUB_TOKEN...")
        subprocess.run(
            ["docker", "login", "ghcr.io", "-u",
             os.environ.get("GITHUB_ACTOR", "github"), "--password-stdin"],
            input=gh_token.encode(), capture_output=True)
    r = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if "ghcr.io" in r.stdout:
        log("SUCCESS", "Authenticated to ghcr.io")
        return
    log("WARN", "Not logged in to ghcr.io. Please run: gh auth login && "
        "gh auth token | docker login ghcr.io -u USERNAME --password-stdin")
    reply = input("Login now with gh? (y/N) ").strip().lower()
    if reply == "y":
        subprocess.run(["gh", "auth", "login"], check=True)
        r = subprocess.run(["gh", "api", "user", "-q", ".login"],
                           capture_output=True, text=True)
        user = r.stdout.strip()
        token = subprocess.run(["gh", "auth", "token"],
                               capture_output=True, text=True).stdout
        subprocess.run(["docker", "login", "ghcr.io", "-u", user,
                        "--password-stdin"], input=token.encode(),
                       check=True)
    else:
        log("ERROR", "GHCR login required. Exiting.")
        sys.exit(1)


def build_and_push(name: str, dockerfile: str, context: str) -> bool:
    image = f"{GHCR_REGISTRY}/{OWNER}/{PROJECT_NAME}:{name}-{TAG}"
    log("INFO", f"Building {name}...")
    log("INFO", f"  Dockerfile: {dockerfile}")
    log("INFO", f"  Context: {context}")
    log("INFO", f"  Target image: {image}")

    if subprocess.run(["docker", "build", "-f", dockerfile,
                       "-t", image, context]).returncode:
        log("ERROR", f"Failed to build {name}")
        return False
    log("SUCCESS", f"Built {image}")

    log("INFO", f"Pushing {image} to GHCR...")
    if subprocess.run(["docker", "push", image]).returncode:
        log("ERROR", f"Failed to push {name}")
        return False
    log("SUCCESS", f"Pushed {image}")

    latest = f"{GHCR_REGISTRY}/{OWNER}/{PROJECT_NAME}:{name}-latest"
    subprocess.run(["docker", "tag", image, latest])
    subprocess.run(["docker", "push", latest])
    log("SUCCESS", f"Tagged and pushed :latest for {name}")
    return True


def update_docker_compose() -> None:
    compose_file = Path("docker-compose.yml")
    backup_file = Path(
        f"docker-compose.yml.backup.{time.strftime('%Y%m%d_%H%M%S')}")
    log("INFO", f"Backing up current docker-compose.yml to {backup_file}")
    shutil.copy(compose_file, backup_file)

    prefix = f"{GHCR_REGISTRY}/{OWNER}/{PROJECT_NAME}"
    log("INFO", f"Updating docker-compose.yml with {prefix}:*-{TAG} images...")
    text = compose_file.read_text()
    for svc in ("comfyui", "env-manager-backend", "env-manager-frontend",
                "social-api", "social-worker", "social-frontend"):
        text = re.sub(rf"image: .*cu130-slim[:\-]{svc}[^ ]*",
                      f"image: {prefix}:{svc}-{TAG}", text)
    compose_file.write_text(text)
    log("SUCCESS", "Updated docker-compose.yml")
    log("INFO", f"Backup saved as: {backup_file}")


SERVICES = [
    ("comfyui", "Dockerfile", "."),
    ("env-manager-backend", "env-manager/backend/Dockerfile",
     "env-manager/backend"),
    ("env-manager-frontend", "env-manager/frontend/Dockerfile",
     "env-manager/frontend"),
    ("social-api", "social-automation/backend/Dockerfile",
     "social-automation/backend"),
    ("social-worker", "social-automation/backend/Dockerfile.worker",
     "social-automation/backend"),
    ("social-frontend", "social-automation/frontend/Dockerfile",
     "social-automation/frontend"),
]

log("INFO", "Starting build, tag, and push for all custom images")
log("INFO", f"GHCR namespace: {GHCR_REGISTRY}/{OWNER}")
log("INFO", f"Project: {PROJECT_NAME}")
log("INFO", f"Tag: {TAG}\n")

check_ghcr_login()

failed = []
for name, dockerfile, context in SERVICES:
    if not build_and_push(name, dockerfile, context):
        failed.append(name)
    print()

update_docker_compose()

print()
log("INFO", "=== BUILD SUMMARY ===")
if not failed:
    log("SUCCESS", "All images built and pushed successfully!")
    log("INFO", "Images pushed:")
    for name, _, _ in SERVICES:
        print(f"  {GHCR_REGISTRY}/{OWNER}/{PROJECT_NAME}:{name}-{TAG}")
    log("INFO", f"Make the single package public at: "
        f"https://github.com/{OWNER}?tab=packages")
else:
    log("ERROR", f"Failed services: {' '.join(failed)}")
    sys.exit(1)
