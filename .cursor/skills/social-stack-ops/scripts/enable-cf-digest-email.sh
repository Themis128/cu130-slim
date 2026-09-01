#!/usr/bin/env bash
# Wire Cloudflare Email Sending for digests (keeps Slack xoxb untouched).
# Usage:
#   CLOUDFLARE_EMAIL_API_TOKEN='…' .devin/skills/social-stack-ops/scripts/enable-cf-digest-email.sh
# Token needs: Account → Email Sending → Edit
# Create at: https://dash.cloudflare.com/profile/api-tokens
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

TOKEN="${CLOUDFLARE_EMAIL_API_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  echo "Set CLOUDFLARE_EMAIL_API_TOKEN env var (Email Sending Edit) before running." >&2
  exit 1
fi

python3 <<'PY'
from pathlib import Path
import os
p = Path(".env")
token = os.environ["CLOUDFLARE_EMAIL_API_TOKEN"].strip()
lines = p.read_text().splitlines()
out = []
seen = {"EMAIL_PROVIDER": False, "CLOUDFLARE_EMAIL_API_TOKEN": False}
for line in lines:
    if line.startswith("EMAIL_PROVIDER="):
        out.append("EMAIL_PROVIDER=cloudflare"); seen["EMAIL_PROVIDER"] = True
    elif line.startswith("CLOUDFLARE_EMAIL_API_TOKEN="):
        out.append(f"CLOUDFLARE_EMAIL_API_TOKEN={token}"); seen["CLOUDFLARE_EMAIL_API_TOKEN"] = True
    else:
        out.append(line)
if not seen["EMAIL_PROVIDER"]:
    out.append("EMAIL_PROVIDER=cloudflare")
if not seen["CLOUDFLARE_EMAIL_API_TOKEN"]:
    out.append(f"CLOUDFLARE_EMAIL_API_TOKEN={token}")
p.write_text("\n".join(out) + "\n")
print("updated .env EMAIL_PROVIDER=cloudflare + CLOUDFLARE_EMAIL_API_TOKEN")
PY

# Preserve shell override pitfalls
unset EMAIL_PROVIDER || true
export EMAIL_PROVIDER=cloudflare
docker compose up -d social-api social-worker-publishing social-worker-media social-worker-default celery-beat --force-recreate
echo "Recreated api/workers/beat. Test: POST /api/v1/ops/daily-digest?post_to_slack=false&post_to_email=true"
