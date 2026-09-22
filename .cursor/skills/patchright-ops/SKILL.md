---
name: patchright-ops
description: >-
  Deploy and verify patchright (undetected Playwright drop-in) across
  SocialAuto's browser automation — browser-novnc bridge, Node sidecars,
  and backend services. Covers the CDP Runtime.enable leak fix, the
  persistent browser_profile volume, and automated verification.
  Use when social-platform mutations silently abort, sessions get
  revoked immediately, or captchas appear on otherwise logged-in flows.
allowed-tools:
  - read
  - exec
  - edit
  - grep
  - find_file_by_name
triggers:
  - user
  - model
---

# Patchright Operations

Patchright (`Kaliiiiiiiiii-Vinyzu/patchright`) is a drop-in fork of
Playwright that removes the `Runtime.enable` CDP leak that marks stock
Playwright as automated. SocialAuto uses it for all browser automation
that platforms commonly detect.

## When to use this skill

- A platform action works in a real browser but silently aborts or
  302-redirects under automation.
- Instagram/TikTok/Meta sessions are minted and revoked within seconds.
- `navigator.webdriver` evaluates to `true` inside the bridge/sidecars.
- You are adding a new browser-automation service or sidecar.
- You rebuilt the `browser-novnc` container and all bridge sessions
  disappeared.

## What was deployed

| Component | Language | Import pattern | Browser install |
|---|---|---|---|
| `browser-novnc` (bridge) | Python | `patchright.async_api` → fallback to `playwright.async_api` | `patchright install chromium` |
| `browser-novnc/extract-cookies.py` | Python | same fallback | bundled with bridge |
| `tiktok-browser-sidecar` | Node | `import { chromium } from "patchright"` | `npx patchright install chromium` |
| `linkedin-browser-sidecar` | Node | same | same |
| `facebook-browser-sidecar` | Node | same | same |
| Backend browser services | Python | same fallback | `playwright>=1.46.0` + `patchright>=1.62.1` |

Backend services:
- `app/services/browser_profile.py`
- `app/services/tiktok_bio_update.py`
- `app/services/tiktok_captcha_analyze.py`
- `app/services/tiktok_captcha_api.py`
- `app/services/tiktok_captcha_debug.py`
- `app/services/tiktok_captcha_debug2.py`
- `app/services/tiktok_captcha_debug3.py`

Dependencies declared in `social-automation/backend/pyproject.toml`.

## The `browser_profile` volume

Before this fix, the Chromium profile lived inside the container layer.
Every `--force-recreate` wiped all platform sessions, forcing a full
re-login cycle via noVNC.

After the fix, `docker-compose.yml` mounts a named volume:

```yaml
volumes:
  browser_profile:/app/browser-profile
```

Sessions now survive `docker compose up -d --force-recreate browser-novnc`.

## Quick verification

Run the bundled script:

```bash
.devin/skills/patchright-ops/scripts/verify.sh
```

It checks:
1. Bridge `/health` and sidecar `/health` endpoints respond.
2. `navigator.webdriver` is `false` inside the bridge.
3. Every Python import fallback compiles.
4. No bare `playwright` imports remain except the intentional fallbacks.

## Adding patchright to a new Python service

```python
try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright
```

Then add to `pyproject.toml`:

```toml
"patchright>=1.62.1",
```

## Adding patchright to a new Node sidecar

`package.json`:

```json
{
  "dependencies": {
    "patchright": "^1.62.1"
  }
}
```

`server.js`:

```js
import { chromium } from "patchright";
```

`Dockerfile`:

```dockerfile
RUN npm install --production --no-cache && npx patchright install chromium
```

## Base image compatibility

Patchright tracks upstream Playwright browser revisions. The sidecar base
images (`mcr.microsoft.com/playwright:v1.*`) install Chromium builds
compatible with the pinned patchright version. Keep `patchright` version
close to the base image's Playwright version to avoid binary/revision
mismatches. The verification script will flag a revision mismatch as a
warning.

## Rollback

If a platform breaks with patchright, temporarily force the old driver
by running the container without patchright installed, or replace the
try/import with a plain playwright import in that single service. Do not
revert the whole stack unless the regression is global.

## References

- GitHub: `Kaliiiiiiiiii-Vinyzu/patchright` — drop-in Playwright fork.
- Original symptom: `Runtime.enable` CDP message leaks `navigator.webdriver`.
- Official Playwright docs for comparison only; SocialAuto runs the
  patched driver.
