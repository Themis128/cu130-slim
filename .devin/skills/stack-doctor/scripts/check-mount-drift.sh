#!/usr/bin/env bash
# diff host file vs the container's mounted copy — the bind-mount drift
# check. usage: check-mount-drift.sh <relative-path-under-app>
set -eu
cd /mnt/c/Users/tbaltzakis/cu130-slim
F="${1:?usage: check-mount-drift.sh <path under app/ e.g. app/api/leads.py>}"
diff <(cat "social-automation/backend/$F") <(docker compose exec -T social-api cat "/app/$F") >/dev/null 2>&1 && echo "MATCH" || { echo "DRIFT — container differs"; diff <(cat "social-automation/backend/$F") <(docker compose exec -T social-api cat "/app/$F") | head -20; }
