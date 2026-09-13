#!/usr/bin/env bash
# Run cloudless.gr unit + e2e tests with correct env.
# Usage: bash ~/cu130-slim/scripts/run-cloudless-tests.sh [unit|e2e|all]
set -euo pipefail

MODE=${1:-all}
REPO="${CLOUDLESS_REPO:-$HOME/cloudless.gr}"
cd "$REPO"

# --- Python venv (cloudless.gr) ---
if [[ ! -f .venv/bin/activate ]]; then
  echo "==> Creating .venv (needs write access to $REPO)"
  if [[ ! -w "$REPO" ]]; then
    echo "Repo not writable (owned by $(stat -c '%U:%G' "$REPO")). Fix with:"
    echo "  sudo chown -R \"\$USER:\$USER\" \"$REPO\""
    exit 1
  fi
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
  pip install -q ruff mypy
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
echo "VIRTUAL_ENV=$VIRTUAL_ENV ($(python -V 2>&1))"

# --- Playwright env ---
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
export LD_LIBRARY_PATH="$HOME/.local/lib/playwright-deps/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

BROWSER="$PLAYWRIGHT_BROWSERS_PATH/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell"
if [[ ! -x "$BROWSER" ]]; then
  echo "==> Installing Playwright Chromium into $PLAYWRIGHT_BROWSERS_PATH"
  pnpm exec playwright install chromium
fi

# Strip secrets that pollute "not configured" unit tests
CLEAN_ENV=(
  env
  -u AUTH_DB -u CF_R2_ACCESS_KEY_ID -u CF_R2_SECRET_ACCESS_KEY -u R2_ACCESS_KEY_ID
  -u R2_SECRET_ACCESS_KEY -u CLOUDFLARE_ACCOUNT_ID -u CF_ACCOUNT_ID
  -u CLOUDFLARE_API_TOKEN -u CLOUDFLARE_AI_API_TOKEN -u WORKERS_AI_ACCOUNT_ID
  -u WORKERS_AI_API_TOKEN -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY
  -u GOOGLE_AI_API_KEY -u DATALAKE_BUCKET -u R2_DATALAKE_BUCKET -u R2_BUCKET_NAME
  -u CF_R2_BUCKET_NAME -u APP_MEDIA_BUCKET -u WHATSAPP_ACCESS_TOKEN
)

run_unit() {
  echo "==> Vitest (unit)"
  "${CLEAN_ENV[@]}" pnpm test:ci
}

run_e2e() {
  echo "==> Freeing :4010 if stale"
  if ss -ltn 2>/dev/null | grep -q ':4010'; then
    pkill -f 'next dev -p 4010' 2>/dev/null || true
    pkill -f 'playwright test --config=playwright.config.mts' 2>/dev/null || true
    sleep 2
  fi
  echo "==> Playwright e2e"
  export PLAYWRIGHT_BROWSERS_PATH LD_LIBRARY_PATH
  pnpm exec playwright test --config=playwright.config.mts --workers=2 --reporter=line
}

case "$MODE" in
  unit) run_unit ;;
  e2e) run_e2e ;;
  all) run_unit; run_e2e ;;
  *) echo "Usage: $0 [unit|e2e|all]"; exit 2 ;;
esac
