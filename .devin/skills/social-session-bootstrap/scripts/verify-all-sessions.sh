#!/usr/bin/env bash
# Verify all social bot sessions and polling task health.
# Usage: verify-all-sessions.sh
set -euo pipefail

cd /home/tbaltzakis/cu130-slim
source .env 2>/dev/null || true

TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
  echo "Error: Could not authenticate with SocialAuto" >&2
  exit 1
fi

echo "═══════════════════════════════════════════════"
echo "  Social Session Bootstrap — Verification"
echo "═══════════════════════════════════════════════"
echo ""

# 1. API health
echo "── API Health ──"
HEALTH=$(curl -s http://localhost:8083/health 2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('status','?'))" 2>/dev/null || echo "FAIL")
echo "  API: $HEALTH"
echo ""

# 2. Container health
echo "── Container Health ──"
for c in social-api social-worker-messenger social-worker-publishing social-worker-media social-worker-default celery-beat; do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$c" 2>/dev/null || echo "missing")
  echo "  $c: $STATUS"
done
echo ""

# 3. Browser sessions
echo "── Browser Sessions (browser-novnc) ──"
for platform in twitter tiktok threads instagram; do
  URL="https://x.com/home"
  case $platform in
    tiktok) URL="https://www.tiktok.com/foryou" ;;
    threads) URL="https://www.threads.com/" ;;
    instagram) URL="https://www.instagram.com/" ;;
  esac
  curl -s -X POST "http://localhost:9223/session/navigate" \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"$URL\"}" >/dev/null 2>&1
  sleep 2
  CURRENT=$(curl -s -X POST "http://localhost:9223/session/evaluate" \
    -H "Content-Type: application/json" \
    -d '{"expression": "window.location.href"}' \
    2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('result','?'))" 2>/dev/null || echo "?")
  echo "  $platform: $CURRENT"
done
echo ""

# 4. Sidecar sessions
echo "── Sidecar Sessions ──"
for port_name in "9225:linkedin" "9226:facebook"; do
  port="${port_name%%:*}"
  name="${port_name##*:}"
  HEALTH=$(curl -s "http://localhost:$port/health" 2>/dev/null | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('status','?'))" 2>/dev/null || echo "down")
  echo "  $name (port $port): $HEALTH"
done
echo ""

# 5. Celery task registration
echo "── Celery Tasks ──"
TASKS=$(docker compose exec -T social-worker-messenger celery -A app.worker.celery_app inspect registered 2>/dev/null | python3 -c "
import sys, json, re
text = sys.stdin.read()
polling = [
  'poll_personal_messenger',
  'poll_instagram_messenger',
  'poll_threads_messenger',
  'poll_twitter_messenger',
  'poll_tiktok_messenger',
  'poll_linkedin_messenger',
  'refresh_instagram_tokens',
  'refresh_linkedin_sessions',
]
for t in polling:
  found = t in text
  print(f'  {t}: {\"✅\" if found else \"❌\"} ')
" 2>/dev/null || echo "  Could not inspect tasks")
echo "$TASKS"
echo ""

# 6. Account token status
echo "── Account Status ──"
curl -s "http://localhost:8083/api/v1/accounts" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
accounts = d if isinstance(d, list) else d.get('accounts', d.get('data', []))
for a in accounts:
  platform = a.get('platform', '?')
  name = a.get('display_name', a.get('username', '?'))
  meta = a.get('meta_data', {}) or {}
  has_token = bool(a.get('access_token_enc'))
  bot = meta.get('bot_config') or meta.get('auto_reply')
  bot_enabled = (bot or {}).get('enabled', False) if bot else False
  print(f'  {platform:12s} | {name:20s} | token={has_token} | bot={bot_enabled}')
" 2>/dev/null || echo "  Could not fetch accounts"
echo ""

echo "═══════════════════════════════════════════════"
echo "  Verification complete"
echo "═══════════════════════════════════════════════"
