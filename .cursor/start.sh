#!/usr/bin/env bash
# Idempotent per-boot startup: brings up PostgreSQL + Redis and provisions the
# social-automation database/role. Safe to re-run.
set -euo pipefail

echo "==> Starting PostgreSQL"
PG_VER="$(ls /etc/postgresql 2>/dev/null | sort -n | tail -1 || true)"
if [ -n "$PG_VER" ]; then
  sudo pg_ctlcluster "$PG_VER" main start 2>/dev/null || true
fi
for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q 2>/dev/null; then break; fi
  sleep 1
done

echo "==> Starting Redis"
if ! redis-cli ping >/dev/null 2>&1; then
  sudo redis-server /etc/redis/redis.conf --daemonize yes || true
fi

echo "==> Provisioning database role and database"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='social_user'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE social_user LOGIN PASSWORD 'social_password';"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='social_automation'" | grep -q 1 \
  || sudo -u postgres createdb -O social_user social_automation
sudo -u postgres psql -d social_automation -c "GRANT ALL ON SCHEMA public TO social_user;" >/dev/null

if ! sudo -u postgres pg_isready -q; then
  echo "PostgreSQL did not become ready" >&2
  exit 1
fi
if ! redis-cli ping | grep -q PONG; then
  echo "Redis did not respond to PING" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/social-automation/backend"
ENV_FILE="$BACKEND_DIR/.env.local"
if [ ! -f "$ENV_FILE" ] || [ ! -x "$BACKEND_DIR/.venv/bin/alembic" ]; then
  echo "Backend venv or .env.local missing. Run .cursor/install.sh first." >&2
  exit 1
fi

echo "==> Applying Alembic migrations"
(
  cd "$BACKEND_DIR"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  # shellcheck disable=SC1091
  . .venv/bin/activate
  alembic upgrade head
)

echo "==> start.sh complete (PostgreSQL + Redis ready, schema current)"
