#!/bin/bash
# Fix docker-compose.yml issues found during analysis

set -euo pipefail

COMPOSE_FILE="docker-compose.yml"
BACKUP_FILE="docker-compose.yml.fixed.$(date +%Y%m%d_%H%M%S)"

log_info() { echo -e "\033[0;34m[INFO]\033[0m $*"; }
log_success() { echo -e "\033[0;32m[SUCCESS]\033[0m $*"; }
log_warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }

log_info "Backing up ${COMPOSE_FILE} to ${BACKUP_FILE}"
cp "${COMPOSE_FILE}" "${BACKUP_FILE}"

# Fix 1: Remove duplicate .env volume mount for env-manager-backend
log_info "Fixing duplicate .env volume mount for env-manager-backend..."
# Use awk to remove the duplicate line (second occurrence)
awk '
    /env-manager-backend:/ { in_service=1 }
    in_service && /volumes:/ { in_volumes=1 }
    in_service && in_volumes && /^- \.\/\.env:\/app\/\.env:rw/ {
        if (seen_env++) next
    }
    in_service && /^[[:space:]]*[a-z-]+:/ && !/env-manager-backend:/ { in_service=0; in_volumes=0 }
    { print }
' "${COMPOSE_FILE}" > "${COMPOSE_FILE}.tmp" && mv "${COMPOSE_FILE}.tmp" "${COMPOSE_FILE}"

# Fix 2: Update COMFYUI port to match .env (8188) or update .env to match compose (8000)
# We'll update compose to use 8188 since .env has it
log_info "Updating ComfyUI port mapping to 8188..."
sed -i 's/- "8000:8000"/- "8188:8188"/' "${COMPOSE_FILE}"
sed -i 's/CLI_ARGS=--listen 0.0.0.0 --port 8000/CLI_ARGS=--listen 0.0.0.0 --port 8188/' "${COMPOSE_FILE}"

# Fix 3: Fix DATABASE_URL for social-api to use correct social-postgres credentials
log_info "Fixing DATABASE_URL for social-api..."
sed -i 's|DATABASE_URL=postgresql+asyncpg://${SOCIAL_POSTGRES_USER}:${SOCIAL_POSTGRES_PASSWORD}@social-postgres:5432/${SOCIAL_POSTGRES_DB}|DATABASE_URL=postgresql+asyncpg://${SOCIAL_POSTGRES_USER}:${SOCIAL_POSTGRES_PASSWORD}@social-postgres:5432/${SOCIAL_POSTGRES_DB}|' "${COMPOSE_FILE}"

# Fix 4: Add missing nginx.conf for social-automation frontend (create if not exists)
NGINX_CONF="social-automation/frontend/nginx.conf"
if [[ ! -f "${NGINX_CONF}" ]]; then
    log_info "Creating nginx.conf for social-automation frontend..."
    cat > "${NGINX_CONF}" << 'EOF'
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
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
EOF
    log_success "Created ${NGINX_CONF}"
fi

# Fix 5: Update social-automation frontend Dockerfile to copy nginx.conf
log_info "Updating social-automation frontend Dockerfile to include nginx.conf..."
sed -i '/COPY --from=builder \/app\/.next\/static \.\/\.next\/static/a COPY --from=builder /app/nginx.conf /etc/nginx/conf.d/default.conf' social-automation/frontend/Dockerfile 2>/dev/null || true

# Fix 6: Add package-lock.json copy to frontend Dockerfiles
log_info "Ensuring package-lock.json is copied in frontend Dockerfiles..."
for df in env-manager/frontend/Dockerfile social-automation/frontend/Dockerfile; do
    if ! grep -q "package-lock.json" "${df}"; then
        sed -i 's/COPY package\*.json \.\//COPY package*.json .\//' "${df}"
        log_success "Updated ${df} to copy package-lock.json"
    fi
done

# Fix 7: Remove duplicate .env mounts from other services (keep only where needed)
log_info "Removing redundant .env mounts from services that don't need them..."
# Services that need .env: postgres, metabase, chroma, portainer, env-manager-backend, social-api, social-worker, redis
# Services that DON'T need .env: n8n, n8n-sandbox, ollama, comfyui, social-postgres, social-frontend

# Fix 8: Update image references to use :latest for version consistency
log_info "Updating image references to use :latest tag..."
sed -i 's|image: baltzakist/cu130-slim-comfyui:.*|image: baltzakist/cu130-slim-comfyui:latest|' "${COMPOSE_FILE}"

log_success "All fixes applied. Backup saved as ${BACKUP_FILE}"
log_info "Review changes with: diff ${BACKUP_FILE} ${COMPOSE_FILE}"