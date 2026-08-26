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
sudo -u postgres psql -d social_automation -c "GRANT ALL ON SCHEMA public TO social_user;" >/dev/null 2>&1 || true

echo "==> start.sh complete (PostgreSQL + Redis ready)"
