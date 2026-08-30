#!/usr/bin/env bash
# Mint / refresh N8N_API_KEY using scripts/init-n8n-api-key.py
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

if [[ ! -f scripts/init-n8n-api-key.py ]]; then
  echo "Missing scripts/init-n8n-api-key.py" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
# shellcheck source=/dev/null
source .env
set +a

export N8N_URL="${N8N_URL:-http://127.0.0.1:5678}"
# Prefer owner email from compose if set
export N8N_USER="${N8N_BASIC_AUTH_USER:-${N8N_USER:-}}"
export N8N_PASSWORD="${N8N_BASIC_AUTH_PASSWORD:-${N8N_PASSWORD:-}}"

echo "Refreshing n8n API key (details not printed)..."
python3 scripts/init-n8n-api-key.py
echo "Done. Restart social-api if deploy-via-API still 401: docker compose restart social-api"
