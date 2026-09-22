#!/usr/bin/env bash
# Verify patchright deployment across browser-novnc, sidecars, and backend consumers.
# Exit 1 if any hard check fails; warnings are printed but non-fatal.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../../../../" && pwd)"
cd "$ROOT" || exit 1

ERR=0
warn() { echo "[WARN] $*"; }
fail() { echo "[FAIL] $*"; ERR=1; }
ok() { echo "[OK]   $*"; }

# 1. Health endpoints
for ep in http://localhost:9223/health http://localhost:9224/health http://localhost:9225/health http://localhost:9226/health; do
  port=${ep#http://localhost:}
  port=${port%%/*}
  if curl -sf -m 5 "$ep" >/dev/null; then
    ok "health $ep"
  else
    fail "health $ep unreachable"
  fi
done

# 2. Bridge webdriver flag must be false
bridge_owner=$(curl -sf -m 5 http://localhost:9223/session/status | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('platform','unknown'))" 2>/dev/null || true)
if [ -n "$bridge_owner" ] && [ "$bridge_owner" != "unknown" ]; then
  wd=$(curl -sf -m 10 -X POST http://localhost:9223/session/evaluate \
    -H "Content-Type: application/json" \
    -H "X-Platform: ${bridge_owner}" \
    -d '{"expression":"navigator.webdriver"}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result','NO_RESULT'))" 2>/dev/null || true)
  if [ "${wd,,}" = "false" ]; then
    ok "navigator.webdriver=false on bridge (owner=${bridge_owner})"
  else
    fail "navigator.webdriver=${wd} on bridge — patchright may not be active"
  fi
else
  warn "could not determine bridge owner; skipping webdriver check"
fi

# 3. Python consumer imports compile
for f in \
  browser-novnc/browser-bridge.py \
  browser-novnc/extract-cookies.py \
  social-automation/backend/app/services/browser_profile.py \
  social-automation/backend/app/services/tiktok_bio_update.py \
  social-automation/backend/app/services/tiktok_captcha_analyze.py \
  social-automation/backend/app/services/tiktok_captcha_api.py \
  social-automation/backend/app/services/tiktok_captcha_debug.py \
  social-automation/backend/app/services/tiktok_captcha_debug2.py \
  social-automation/backend/app/services/tiktok_captcha_debug3.py; do
  if python3 -m py_compile "$f" 2>/dev/null; then
    ok "compile $f"
  else
    fail "compile $f"
  fi
done

# 4. Python patchright available inside social-api
if docker compose exec -T social-api python3 -c "from patchright.async_api import async_playwright; print('patchright ok')" 2>/dev/null | grep -q patchright; then
  ok "patchright import inside social-api"
else
  fail "patchright not importable inside social-api"
fi

# 5. Sidecars run patchright package (Node)
for svc in tiktok-browser-sidecar linkedin-browser-sidecar facebook-browser-sidecar; do
  if docker exec "$svc" sh -c 'node -e "import(\"patchright\").then(()=>console.log(\"ok\"))"' 2>/dev/null | grep -q ok; then
    ok "patchright node import inside $svc"
  else
    fail "patchright not importable inside $svc"
  fi
done

# 6. No stray bare playwright imports in consumers (exclude intentional fallbacks)
# We accept the fallback lines: "except ImportError:" preceding "from playwright.async_api"
# and the standalone scripts/extract-cookies.py which now also has the fallback.
violations=$(grep -rn "from playwright.async_api\|from playwright" \
  browser-novnc/browser-bridge.py \
  browser-novnc/extract-cookies.py \
  social-automation/backend/app/services/browser_profile.py \
  social-automation/backend/app/services/tiktok_bio_update.py \
  social-automation/backend/app/services/tiktok_captcha_analyze.py \
  social-automation/backend/app/services/tiktok_captcha_api.py \
  social-automation/backend/app/services/tiktok_captcha_debug.py \
  social-automation/backend/app/services/tiktok_captcha_debug2.py \
  social-automation/backend/app/services/tiktok_captcha_debug3.py \
  tiktok-browser-sidecar/server.js \
  linkedin-browser-sidecar/server.js \
  facebook-browser-sidecar/server.js \
  2>/dev/null | grep -v "except ImportError" | grep -v "fallback" | grep -v "standalone" || true)

# Better heuristic: lines that import playwright but not inside an except ImportError block.
strays=$(python3 - <<'PY' || true
import re
files = [
    "browser-novnc/browser-bridge.py",
    "browser-novnc/extract-cookies.py",
    *__import__("glob").glob("social-automation/backend/app/services/tiktok_*.py"),
    "social-automation/backend/app/services/browser_profile.py",
    "tiktok-browser-sidecar/server.js",
    "linkedin-browser-sidecar/server.js",
    "facebook-browser-sidecar/server.js",
]
for path in files:
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        continue
    except_indent = None
    for i, line in enumerate(lines):
        if except_indent is not None:
            if not line.strip() or line.lstrip().startswith('#'):
                continue  # blank/comment inside block
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= except_indent:
                except_indent = None  # left the except block
        if except_indent is None and re.search(r'^\s*except\s+ImportError', line):
            except_indent = len(line) - len(line.lstrip())
            continue
        if re.search(r'from\s+playwright', line) and except_indent is None:
            print(f"{path}:{i+1}: {line.strip()}")
PY
)

if [ -n "$strays" ]; then
  fail "bare playwright imports found:\n$strays"
else
  ok "no bare playwright imports in consumers"
fi

# 7. browser_profile volume present in compose
if docker compose config 2>/dev/null | grep -q "browser_profile:"; then
  ok "browser_profile volume declared in compose"
else
  fail "browser_profile volume missing from compose"
fi

# 8. browser-novnc container actually mounts the profile
if docker inspect browser-novnc --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}' 2>/dev/null | grep -q "^/app/browser-profile$"; then
  ok "browser-novnc mounts /app/browser-profile"
else
  fail "browser-novnc does not mount /app/browser-profile"
fi

if [ "$ERR" -eq 0 ]; then
  echo "[ALL OK] patchright deployment verified"
else
  echo "[DONE] some checks failed"
fi
exit "$ERR"
