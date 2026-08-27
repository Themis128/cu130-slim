#!/usr/bin/env bash
# Print health of Cloudless social stack services (no secrets).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

echo "== containers =="
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' \
  | grep -E 'NAMES|social|n8n|redis|comfy|ollama|chroma|postgres' || true

echo
echo "== http probes =="
probe() {
  local name="$1" url="$2"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" || echo fail)"
  echo "$name  $code  $url"
}

probe n8n_healthz http://127.0.0.1:5678/healthz
probe social_openapi http://127.0.0.1:8083/openapi.json
probe social_frontend http://127.0.0.1:8082/

echo
echo "== carousel endpoint present? =="
if curl -sf http://127.0.0.1:8083/openapi.json >/tmp/social-openapi.json 2>/dev/null; then
  python3 -c 'import json; p=json.load(open("/tmp/social-openapi.json")).get("paths",{});
print("run-carousel-and-publish", "/api/v1/ai/run-carousel-and-publish" in p)
print("generate-carousel-pipeline", "/api/v1/ai/generate-carousel-pipeline" in p)'
else
  echo "(social-api openapi unreachable)"
fi

echo
echo "== n8n active workflows =="
docker exec n8n n8n list:workflow --active=true 2>/dev/null || echo "(n8n CLI unavailable)"
