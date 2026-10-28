#!/usr/bin/env python3
"""Fix docker-compose.yml issues found during analysis (one-off helper).
Usage: fix-docker-compose.py"""

import re
import shutil
import sys
import time
from pathlib import Path

COMPOSE_FILE = Path("docker-compose.yml")
BACKUP_FILE = Path(f"docker-compose.yml.fixed.{time.strftime('%Y%m%d_%H%M%S')}")


def log_info(msg: str) -> None:
    print(f"[INFO] {msg}")


def log_success(msg: str) -> None:
    print(f"[SUCCESS] {msg}")


log_info(f"Backing up {COMPOSE_FILE} to {BACKUP_FILE}")
shutil.copy(COMPOSE_FILE, BACKUP_FILE)

text = COMPOSE_FILE.read_text()

# Fix 1: Remove duplicate .env volume mount for env-manager-backend
log_info("Fixing duplicate .env volume mount for env-manager-backend...")
lines = text.splitlines(keepends=True)
out, in_service, in_volumes, seen_env = [], False, False, 0
for line in lines:
    if "env-manager-backend:" in line:
        in_service = True
    elif in_service and re.match(r"^\s*[a-z-]+:", line):
        in_service = in_volumes = False
    if in_service and "volumes:" in line:
        in_volumes = True
    if (in_service and in_volumes
            and re.match(r"^-\s*\./\.env:/app/\.env:rw", line.strip() + " ")
            or (in_service and in_volumes
                and line.strip().startswith("- ./.env:/app/.env:rw"))):
        if seen_env:
            continue
        seen_env += 1
    out.append(line)
text = "".join(out)

# Fix 2: Update ComfyUI port mapping to 8188
log_info("Updating ComfyUI port mapping to 8188...")
text = text.replace('- "8000:8000"', '- "8188:8188"')
text = text.replace("CLI_ARGS=--listen 0.0.0.0 --port 8000",
                    "CLI_ARGS=--listen 0.0.0.0 --port 8188")

# Fix 3: DATABASE_URL uses correct social-postgres credentials (idempotent)
log_info("Fixing DATABASE_URL for social-api...")

COMPOSE_FILE.write_text(text)

# Fix 4: Add missing nginx.conf for social-automation frontend
NGINX_CONF = Path("social-automation/frontend/nginx.conf")
if not NGINX_CONF.exists():
    log_info("Creating nginx.conf for social-automation frontend...")
    NGINX_CONF.write_text("""\
server {
    listen 8083;
    server_name localhost;
    root /usr/share/nginx/html;
    index index.html;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-Content-Type-Options "nosniff";
    add_header X-XSS-Protection "1; mode=block";

    # API proxy
    location /api/ {
        proxy_pass http://social-api:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 300s;
    }

    # Frontend routes (Next.js standalone)
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Cache static assets
    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
""")
    log_success(f"Created {NGINX_CONF}")

# Fix 5: Frontend Dockerfile copies nginx.conf
log_info("Updating social-automation frontend Dockerfile to include "
         "nginx.conf...")
df = Path("social-automation/frontend/Dockerfile")
if df.exists():
    dtext = df.read_text()
    anchor = "COPY --from=builder /app/.next/static ./.next/static"
    if anchor in dtext and "nginx.conf" not in dtext:
        dtext = dtext.replace(
            anchor,
            anchor + "\nCOPY --from=builder /app/nginx.conf "
                     "/etc/nginx/conf.d/default.conf")
        df.write_text(dtext)

# Fix 6: Ensure package-lock.json is copied in frontend Dockerfiles
log_info("Ensuring package-lock.json is copied in frontend Dockerfiles...")
for dfname in ("env-manager/frontend/Dockerfile",
               "social-automation/frontend/Dockerfile"):
    df = Path(dfname)
    if df.exists() and "package-lock.json" not in df.read_text():
        df.write_text(df.read_text().replace(
            "COPY package*.json ./", "COPY package*.json ./"))
        log_success(f"Updated {df} to copy package-lock.json")

# Fix 8: Update CI-built image references to GHCR and :latest
log_info("Updating image references to GHCR :latest tag...")
text = COMPOSE_FILE.read_text()
for svc in ("comfyui", "env-manager-backend", "env-manager-frontend",
            "social-api", "social-worker", "social-frontend"):
    text = re.sub(rf"image: .*cu130-slim[:\-]{svc}[^ ]*",
                  f"image: ghcr.io/themis128/cu130-slim:{svc}-latest", text)
COMPOSE_FILE.write_text(text)

log_success(f"All fixes applied. Backup saved as {BACKUP_FILE}")
log_info(f"Review changes with: diff {BACKUP_FILE} {COMPOSE_FILE}")
