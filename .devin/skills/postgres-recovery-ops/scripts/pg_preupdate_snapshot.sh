#!/usr/bin/env bash
# Pre-mutation snapshot for SocialAuto Postgres — run BEFORE any manual
# UPDATE/DELETE. Creates <table>_predrop_<yyyymmdd> as a full copy so a bad
# statement can be rolled back row-by-row without WAL/heap surgery.
#
# Usage (WSL, repo root):
#   bash .devin/skills/postgres-recovery-ops/scripts/pg_preupdate_snapshot.sh social_accounts
#   bash .devin/skills/postgres-recovery-ops/scripts/pg_preupdate_snapshot.sh social_accounts posts publish_queue
#
# Idempotent-ish: refuses to overwrite an existing snapshot of the same name
# unless SNAPSHOT_FORCE=1. Never drops anything.
set -euo pipefail

DB="${PGDB:-social_automation}"
USER="${PGUSER:-social_user}"
CTR="${PGCTR:-social-postgres}"
STAMP="$(date +%Y%m%d)"

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <table> [table...]" >&2
  exit 2
fi

for t in "$@"; do
  [[ "$t" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || { echo "bad table name: $t" >&2; exit 2; }
  snap="${t}_predrop_${STAMP}"
  exists=$(docker exec "$CTR" psql -U "$USER" -d "$DB" -Atc \
    "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='$snap';")
  if [ "$exists" = "1" ] && [ "${SNAPSHOT_FORCE:-0}" != "1" ]; then
    echo "SKIP $t -> $snap already exists (SNAPSHOT_FORCE=1 to replace)"
    continue
  fi
  if [ "$exists" = "1" ]; then
    docker exec "$CTR" psql -U "$USER" -d "$DB" -c "DROP TABLE $snap;" >/dev/null
  fi
  docker exec "$CTR" psql -U "$USER" -d "$DB" -c \
    "CREATE TABLE $snap AS SELECT * FROM $t;"
  cnt=$(docker exec "$CTR" psql -U "$USER" -d "$DB" -Atc "SELECT count(*) FROM $snap;")
  echo "OK $t -> $snap ($cnt rows)"
done
