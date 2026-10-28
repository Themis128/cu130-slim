#!/usr/bin/env bash
# Idempotent dependency setup for the Social Automation Platform.
# Stable system packages live in .cursor/Dockerfile; this script only refreshes
# repository-tied Python/Node deps and a gitignored local env file.
# Falls back to apt only when the base image is missing PostgreSQL/Redis
# (e.g. running on Cursor's default image before a Dockerfile build is active).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/social-automation/backend"
FRONTEND_DIR="$REPO_ROOT/social-automation/frontend"

if ! command -v psql >/dev/null 2>&1 || ! command -v redis-server >/dev/null 2>&1; then
  echo "==> System packages missing; installing PostgreSQL, Redis, and build toolchain"
  export DEBIAN_FRONTEND=noninteractive
  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    postgresql postgresql-contrib redis-server \
    python3-venv python3-dev build-essential libpq-dev \
    libjpeg-dev zlib1g-dev libde265-dev libheif-dev
else
  echo "==> System packages already present (Dockerfile/base image); skipping apt"
fi

# Cloud Agent VMs can expose an older Node earlier on PATH (for example
# /exec-daemon/node). Prefer the NodeSource/distro binary in /usr/bin.
prefer_usr_bin_node() {
  if [ -x /usr/bin/node ]; then
    export PATH="/usr/bin:${PATH}"
  fi
}
prefer_usr_bin_node

# Frontend package.json engines require Node >= 22.22.2. The Dockerfile
# installs current Node 22; this covers Cursor's default image, which may lag.
NODE_MIN="22.22.2"
node_ok=0
if command -v node >/dev/null 2>&1; then
  node_current="$(node -v | sed 's/^v//')"
  if [ "$(printf '%s\n' "$NODE_MIN" "$node_current" | sort -V | head -1)" = "$NODE_MIN" ]; then
    node_ok=1
  fi
fi
if [ "$node_ok" -eq 0 ]; then
  echo "==> Installing Node.js 22 (frontend requires >= ${NODE_MIN})"
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
  prefer_usr_bin_node
fi
echo "==> Node $(node -v) ($(command -v node))"

echo "==> Setting up backend virtualenv and dependencies"
cd "$BACKEND_DIR"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip -q
# Dev extras provide pytest, ruff, and mypy used by the local test gate.
pip install -e ".[dev]" -q
deactivate

mkdir -p "$BACKEND_DIR/uploads"

# Generate a local (non-Docker) dev env file if it does not exist.
# This file is gitignored and contains DEV-ONLY placeholder secrets.
if [ ! -f "$BACKEND_DIR/.env.local" ]; then
  echo "==> Writing backend/.env.local (dev-only placeholders)"
  cat > "$BACKEND_DIR/.env.local" <<ENV
DATABASE_URL=postgresql+asyncpg://social_user:social_password@localhost:5432/social_automation
REDIS_URL=redis://localhost:6379/0
MESSENGER_REDIS_URL=redis://localhost:6379/1
APP_ENV=development
DEBUG=true
# Postgres is the local store. Cloudflare D1/KV/Vectorize stay off until tokens are set.
D1_ENABLED=false
JWT_SECRET_KEY=dev-jwt-secret-key-change-me-min-32-chars
ENCRYPTION_KEY=dev-encryption-key-32-bytes-min!!
SOCIAL_ADMIN_EMAIL=admin@example.com
SOCIAL_ADMIN_PASSWORD=admin_password_123
SOCIAL_ADMIN_NAME="Admin User"
UPLOAD_DIR=$BACKEND_DIR/uploads
N8N_API_URL=http://localhost:5678
COMFYUI_URL=http://localhost:8000
CHROMA_URL=http://localhost:8001
OLLAMA_URL=http://localhost:11434
ENV
fi

echo "==> Installing frontend dependencies"
cd "$FRONTEND_DIR"
# --legacy-peer-deps: vitest/vite peer range vs @vitejs/plugin-react (dev tooling only)
npm ci --legacy-peer-deps

echo "==> install.sh complete"
