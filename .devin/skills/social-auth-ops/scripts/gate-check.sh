#!/usr/bin/env bash
# gate-check.sh — report every social-auth credential's live state.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

check() { # name envvar
  v=$(grep -E "^$2=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r"')
  if [ -n "$v" ] && [[ "$v" != *"<"* ]]; then echo "  ✓ $1"; else echo "  ✗ $1  (empty)"; fi
}
echo "== X/Twitter =="
check "TWITTER_CLIENT_ID" TWITTER_CLIENT_ID
check "TWITTER_CLIENT_SECRET" TWITTER_CLIENT_SECRET
check "TWITTER_API_KEY" TWITTER_API_KEY
echo "== TikTok =="
check "TIKTOK_CLIENT_KEY" TIKTOK_CLIENT_KEY
check "TIKTOK_CLIENT_SECRET" TIKTOK_CLIENT_SECRET
echo "== Email =="
check "EMAIL_PROVIDER" EMAIL_PROVIDER
check "SMTP_HOST" SMTP_HOST
check "SMTP_PASSWORD" SMTP_PASSWORD
check "CLOUDFLARE_EMAIL_API_TOKEN" CLOUDFLARE_EMAIL_API_TOKEN
echo "== Last token-refresh errors =="
docker compose logs --since 24h social-worker-publishing 2>/dev/null \
  | grep -iE "token refresh failed|no access_token" | tail -4 || true
