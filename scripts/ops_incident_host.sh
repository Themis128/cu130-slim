#!/usr/bin/env bash
# ops_incident_host.sh — report an ops incident WITHOUT social-api.
#
# Fallback for the detector wiring (compose-watchdog, social-stack-watchdog)
# when the social-api container is down — exactly the outages that matter
# most. Writes straight into ops_incidents via social-postgres psql with the
# same upsert semantics as app/services/ops_agent.py::report_incident
# (fingerprint dedup, reopen-on-recurrence, recurrence>=2 -> critical).
# Keep the ON CONFLICT clause in sync with that function.
#
# Usage: ops_incident_host.sh <source> <kind> <severity> <title> [target] [detail-json]
# Creds come from .env (SOCIAL_POSTGRES_*); requires docker + social-postgres up.
set -u
cd "$(dirname "$0")/.." || exit 1

SRC="${1:?source required}"; KIND="${2:?kind required}"; SEV="${3:-ops}"
TITLE="${4:?title required}"; TARGET="${5:-}"; DETAIL="${6:-{}}"
case "$SEV" in info|ops|critical) ;; *) SEV=ops ;; esac

FP=$(printf '%s|%s|%s' "$SRC" "$KIND" "$TARGET" | sha256sum | cut -c1-32)

getenv() { grep -oP "^$1=\\K.*" .env 2>/dev/null | tr -d "\"'" | head -1; }
PGUSER=$(getenv SOCIAL_POSTGRES_USER); PGPASS=$(getenv SOCIAL_POSTGRES_PASSWORD); PGDB=$(getenv SOCIAL_POSTGRES_DB)
[ -n "$PGUSER" ] && [ -n "$PGDB" ] || { echo "ops_incident_host: missing SOCIAL_POSTGRES_* in .env" >&2; exit 1; }

docker exec -i -e PGPASSWORD="$PGPASS" social-postgres \
  psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 -q \
  -v fp="$FP" -v src="$SRC" -v kind="$KIND" -v target="$TARGET" \
  -v sev="$SEV" -v title="$TITLE" -v detail="$DETAIL" <<'SQL'
INSERT INTO ops_incidents
  (id, fingerprint, source, kind, target, severity, title, detail, status,
   recurrence_count, agent_attempts, rca, proposed_action, action_taken,
   action_result, first_seen, last_seen, created_at, updated_at)
VALUES
  (gen_random_uuid(), :'fp', :'src', :'kind', :'target', :'sev', :'title',
   :'detail'::jsonb, 'open', 0, 0, '', '', '', '', now(), now(), now(), now())
ON CONFLICT (fingerprint) DO UPDATE SET
  last_seen = now(),
  updated_at = now(),
  title = EXCLUDED.title,
  detail = ops_incidents.detail || EXCLUDED.detail,
  recurrence_count = CASE WHEN ops_incidents.status = 'resolved'
                          THEN ops_incidents.recurrence_count + 1
                          ELSE ops_incidents.recurrence_count END,
  status = CASE WHEN ops_incidents.status = 'resolved' THEN 'open'
                ELSE ops_incidents.status END,
  resolved_at = CASE WHEN ops_incidents.status = 'resolved' THEN NULL
                     ELSE ops_incidents.resolved_at END,
  severity = CASE
    WHEN EXCLUDED.severity = 'critical' OR ops_incidents.severity = 'critical' THEN 'critical'
    WHEN (CASE WHEN ops_incidents.status = 'resolved'
               THEN ops_incidents.recurrence_count + 1
               ELSE ops_incidents.recurrence_count END) >= 2 THEN 'critical'
    ELSE ops_incidents.severity END;
SQL
