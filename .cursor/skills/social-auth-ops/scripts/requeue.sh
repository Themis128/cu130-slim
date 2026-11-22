#!/usr/bin/env bash
# requeue.sh <post_id> <platform> — reset a failed publish_queue row to pending.
set -euo pipefail
# sanitize SQL-interpolated args — post_id is a UUID, platform is a slug
[[ "$1" =~ ^[0-9a-fA-F-]{36}$ ]] || { echo "bad post_id"; exit 1; }
[[ "${2:-}" =~ ^[a-z_]{2,20}$ ]] || { echo "bad platform"; exit 1; }
cd "$(git rev-parse --show-toplevel)"
docker compose exec -T social-postgres psql -U social_user -d social_automation -c \
 "update publish_queue set status='pending', attempts=0, locked_at=null, locked_by=null from social_accounts a where publish_queue.post_id='$1' and a.id=publish_queue.social_account_id and a.platform='$2'"
docker compose exec -T social-worker-publishing celery -A app.worker.celery_app call app.worker.tasks.publishing.process_publish_queue >/dev/null
echo "requeued $2 for $1"
