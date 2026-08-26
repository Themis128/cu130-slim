#!/usr/bin/env bash
# Idempotent dependency setup for the Social Automation Platform dev environment.
# Runs after the repository is checked out. Safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/social-automation/backend"
FRONTEND_DIR="$REPO_ROOT/social-automation/frontend"

echo "==> Installing system packages (PostgreSQL, Redis, build toolchain)"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq \
  postgresql postgresql-contrib redis-server \
  python3-venv python3-dev build-essential libpq-dev \
  libjpeg-dev zlib1g-dev libde265-dev libheif-dev

echo "==> Setting up backend virtualenv and dependencies"
cd "$BACKEND_DIR"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -e . -q
deactivate

mkdir -p "$BACKEND_DIR/uploads"

# Generate a local (non-Docker) dev env file if it does not exist.
# This file is gitignored and contains DEV-ONLY placeholder secrets.
if [ ! -f "$BACKEND_DIR/.env.local" ]; then
  echo "==> Writing backend/.env.local (dev-only placeholders)"
  cat > "$BACKEND_DIR/.env.local" <<ENV
DATABASE_URL=postgresql+asyncpg://social_user:social_password@localhost:5432/social_automation
REDIS_URL=redis://localhost:6379/0
DEBUG=true
JWT_SECRET_KEY=dev-jwt-secret-key-change-me-min-32-chars
ENCRYPTION_KEY=dev-encryption-key-32-bytes-min!!
SOCIAL_ADMIN_EMAIL=admin@example.com
SOCIAL_ADMIN_PASSWORD=admin_password_123
SOCIAL_ADMIN_NAME=Admin User
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
