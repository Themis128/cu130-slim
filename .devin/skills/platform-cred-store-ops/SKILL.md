---
name: platform-cred-store-ops
description: Strategy + tooling for social-platform login credentials managed ONLY through the SocialAuto secret store. Covers the canonical write path (POST /api/v1/secrets/{key}), the read path (secret_store.get / API), the platform-cred key inventory, the .env-as-mirror rule, and the drift audit (scripts/cred_audit.py). Use when rotating any platform password/TOTP/session credential, when .env files diverge on platform creds, or when an agent needs to store a new social login secret.
---

# Platform credential store ops

**Rule (user directive 2026-11-22): credentials for social-media platform
logins are written ONLY via SocialAuto — never by hand-editing `.env`
files.** `.env` files are read-only mirrors produced by the store.

## Why (the incident that created this)

2026-11-22: Meta password changed on the phone. Two `.env` files carried
*different* `FACEBOOK_PASSWORD`/`INSTAGRAM_PASSWORD` values — neither
matched the new password. Agents hand-editing `.env` produced silent
divergence; `sidecar-relogin.py` (reads root `.env`) and containers
(read `deploy/socialauto-standalone/.env` via `env_file:`) used different
credentials. Instagram login failed with both stored values.

## Architecture

```
WRITE (only path)
  agent/human → POST /api/v1/secrets/{KEY}  (Bearer admin token)
                     │
                     ▼
        app/services/secret_store.py :: SecretStore.set()
                     │
        ┌────────────┼─────────────┐
        ▼            ▼             ▼
   Cloudflare D1   Postgres    /app/.env (container)
   social_secrets  social_secrets   best-effort sync
        │            │             │
        └──── read order: D1 → PG → env ────┘
                     │
        GET /api/v1/secrets/{KEY} → raw value
```

- `POST`/`PUT /api/v1/secrets/{key}` — upsert; response `sources` shows
  which stores accepted the write.
- `GET /api/v1/secrets` — list keys (values masked).
- `GET /api/v1/secrets/{key}` — raw value (admin auth required).
- In-app code: `from app.services.secret_store import secret_store;
  await secret_store.get(KEY)` — never `os.environ` for platform creds.
- External scripts: `skill_http.get_secret(KEY)` / `set_secret(KEY, val)`
  (`.devin/skills/_lib/skill_http.py`) — API first.

## Platform-cred key inventory

Keys that MUST go through the store (passwords, second factors, session
material, OAuth client credentials for social platforms):

| Platform | Keys |
|---|---|
| Meta/FB/IG/Threads/WA | `FACEBOOK_PASSWORD`, `INSTAGRAM_PASSWORD`, `META_TOTP_SECRET`, `META_TOTP_SECRET_IG`, `WHATSAPP_*` tokens, `FACEBOOK_*`/`INSTAGRAM_*` OAuth client id/secret |
| LinkedIn | `LINKEDIN_PASSWORD`, `LINKEDIN_*` OAuth client creds, session blobs |
| TikTok | `TIKTOK_DEV_PASSWORD`, `TIKTOK_*` client creds |
| X/Twitter | `TWITTER_*`/`X_*` password + TOTP (`TWITTER_TOTP_SECRET`) + OAuth |
| Google (brand acct) | `GOOGLE_*` passwords/app passwords used for platform login |
| Store-only session material | `FB_USER_TOKEN`, `LINKEDIN_MEMBER_DP_TOKEN`, `MESSENGER_VERIFY_TOKEN`, `SKOOL_PASSWORD`, `TWITTER_ACCESS_TOKEN`, `TWITTER_ACCESS_TOKEN_SECRET`, `TWITTER_API_SECRET` — live in the store only, never mirrored to `.env` (tracked as `KNOWN_STORE_ONLY_KEYS` in cred_audit.py) |

Infra secrets (DB passwords, API keys, broker creds) stay in Infisical —
the store's env-fallback still resolves them, but they are NOT platform
login creds and are out of scope for this rule.

## The .env mirror rule

- `deploy/socialauto-standalone/.env` is what `env_file:` injects into
  containers at **create** time — env changes need `compose up -d`
  (recreate), never `docker restart`.
- The store's `_sync_env_file` writes `/app/.env` **inside the container**
  — that is the container's own copy, updated on every `set()`.
- Host-side `.env` files (repo root + standalone) are bootstrap/mirror
  copies for host-run scripts (`sidecar-relogin.py`, `ig_login.py`).
  Hand-editing platform-cred keys there is FORBIDDEN — it recreates the
  divergence this skill exists to kill.
- Transitional reality: host scripts still read `.env` today. Until they
  are rewired to the API, mirror writes must keep all host copies in
  parity — use `cred_audit.py --fix-mirror` after any store write.

## Workflow: rotate/store a platform credential

```bash
# THE write tool — store write + mirror realign + hash verify in one step.
# Value via getpass/stdin, NEVER argv (shell history leaks secrets).
python3 .devin/skills/platform-cred-store-ops/scripts/cred_set.py INSTAGRAM_PASSWORD
python3 .devin/skills/platform-cred-store-ops/scripts/cred_set.py INSTAGRAM_PASSWORD --value-stdin --desc "rotation 2026-11-22"

# Manual equivalent (what cred_set.py does internally):
python3 - <<'EOF'
import sys; sys.path.insert(0, '.devin/skills/_lib')
from skill_http import set_secret
import getpass
set_secret("INSTAGRAM_PASSWORD", getpass.getpass("new value: "),
           "Meta IG password rotation 2026-11-22")
EOF

# Audit drift across store + both .env mirrors
python3 .devin/skills/platform-cred-store-ops/scripts/cred_audit.py

# If host mirrors diverge outside a cred_set run, align them to the store
python3 .devin/skills/platform-cred-store-ops/scripts/cred_audit.py --fix-mirror

# Consumers pick it up:
#    - in-app: next secret_store.get() call (no restart needed)
#    - containers: env_file vars need recreate — deploy-watch or
#      `compose up -d --force-recreate <svc>` if the key is env-injected
#    - host scripts: read .env on each run — immediate
```

## Verify tool

`scripts/cred_verify.py` — live-probes whether the canonical credential
actually works (the gap that let a converged-but-wrong Meta password sit
undetected):

- `facebook` → real `POST :9226/login` attempt on the sidecar (interval-
  gated, default 6h min between probes — Meta login hammering triggers
  checkpoints; `--force` overrides)
- `instagram` → `ig_login.py --headless` via `--ig-login` (opt-in:
  headless IG logins carry checkpoint risk)
- `--status` prints last results; exit 1 on REJECTED/ERROR/UNCLEAR.

Converged hashes ≠ working credential — run this after any Meta
credential change before declaring the rotation done.

### Verified Meta login facts (2026-11-22, corrected same-day)

- **Meta Account 2FA has NO authenticator-app/TOTP option** in the
  observed flow — only Notification-on-device, WhatsApp, SMS
  (Text message), and Backup code. `META_TOTP_SECRET` /
  `META_TOTP_SECRET_IG` TOTP codes were both rejected ("code isn't
  right"); do not burn attempts on them for Meta Account login.
- **Working unattended 2FA lane = SMS**: sidecar `/login` (or
  `/debug/eval` drive) → "Try another way" → "See more options" →
  "Text message" row → Continue (click the LAST Continue — the method
  panel has its own) → code arrives as a Google-Messages notification
  readable via `adb shell dumpsys notification --noredact` →
  `phone-2fa-ops/scripts/fb_code.py` (regex covers "NNNNNN is your
  Facebook/Instagram code") → fill via native setter → Continue.
  Verified end-to-end: full feed load, no checkpoint.
- **`cred_verify.py` semantics**: `OK(2fa-required)` = password
  ACCEPTED (credential valid, only 2FA pending) — not a failure.
  `REJECTED` = bad password. Check `/debug/page-text` for the real
  surface before assuming.
- **Forced-reset checkpoint** ("secure your account → create a new
  password") was completed 2026-11-22 ~20:32 EET by the parallel
  agent's `credential-reset-ops/ig_pw_reset.py` drive — it logs out
  all devices; the new password must then go through `cred_set.py`
  immediately (done: store+3 mirrors converged, hash 1e3e2be4b4f0).
- **`instagram_manage_contents` is a valid FB-Login-for-Business
  permission but must be enabled in the app's Login Configuration**
  (App Dashboard → Facebook Login for Business → Settings). Without
  it the authorize URL returns `error_code=100 Invalid Scopes` and
  the whole OAuth callback dies — keep it OUT of scope lists until
  the config grants it, or every Meta re-auth breaks.
- Checkpoint gate: after password+2FA, Meta can force
  `/checkpoint/.../secure your account` → "Create a new password" →
  `auth_platform/reset_password`. Completing it **logs out every
  device** (phone included). Abort by navigating away unless a full
  reset is intended; if completed, immediately `cred_set.py` the new
  password — it is the new canonical value.

## Audit tool

`scripts/cred_audit.py` — compares every platform-cred key across:

1. SocialAuto secret store (via `GET /api/v1/secrets/{key}`)
2. repo-root `.env`
3. `deploy/socialauto-standalone/.env`

Reports per key: `OK` (all sources agree), `DIVERGED` (hashes differ —
values never printed), `MISSING` (absent from a source). `--fix-mirror`
copies the store value into any diverging host `.env` (store wins — it is
canonical). Exit 1 on divergence.

## Consumer migration status

| Consumer | Reads | Status |
|---|---|---|
| in-app workers/api | `secret_store.get()` / env fallback | ✅ canonical already |
| `sidecar-relogin.py` (home-ops) | root `.env` | ⏳ phase 2 — API-first rewrite |
| `ig_login.py` | root `.env` | ⏳ phase 2 — API-first rewrite |
| `instagram_reauth.py` | admin login + sidecar | ✅ no password needed (OAuth lane) |

Phase 2 (tracked in BACKLOG): rewire host scripts to `get_secret()` with
`.env` fallback so the store becomes the single read path everywhere.

## Browser auth probe + secret sync (2026-11-22 battle test)

`scripts/browser_auth_probe.py` — verifies REAL auth state on the debug
Chrome CDP relay (`http://192.168.96.1:9229` by default). Classifies each
platform tab as AUTHENTICATED / LOGIN-WALL / PROFILE-PICKER / CHECKPOINT /
UNKNOWN from visible page text — cookie presence alone proves nothing
(injected cookies can be stale or rejected). `--click-continue` attempts the
one-tap profile-picker login and re-probes. Requires the igreset venv
(`websocket-client`): `/home/tbaltzakis/.venv-igreset/bin/python`.

`scripts/browser_secret_sync.py` — regenerates the Chrome/Edge password CSV
(`C:\Users\tbaltzakis\Downloads\browser-passwords-import.csv`, 8 social rows)
from canonical cred keys, `--cookies` injects session cookies into debug
Chrome, `--check` audits drift. Chrome/Edge password stores have no write
API — CSV import is the only bulk path (manual file-picker click per browser).

Battle-test findings (2026-11-22):

- **X session cookies**: canonical fresh set is `C:\Users\tbaltzakis\x_cookies.json`
  (matches `X_WEB_AUTH_TOKEN`). `.env X_WEB_COOKIES_JSON` was STALE — the
  pre-rotation session; store/envs now carry the fresh JSON. Inject →
  `x.com/home` verified AUTHENTICATED.
- **Meta one-tap pickers are zombie sessions**: IG `t_baltzakis` and FB
  `Themistoklis Baltzakis` profile pickers render but Continue never
  authenticates — Meta invalidated the saved sessions. Only a fresh
  password+2FA login revives them.
- **Bluesky**: `BSKY_MAIN_PASSWORD` is the account MAIN password — it only
  authenticates with the EMAIL identifier (`baltzakis.themis@gmail.com`),
  not the handle (`createSession` 401s on handle). `BSKY_CREDENTIALS` app
  password was stale (401); rotated to `sa-rot-1795383255` via
  `createAppPassword` using a main-password session — verified. App-password
  JWTs (`scope com.atproto.appPass`) CANNOT call `listAppPasswords`/
  `createAppPassword`/`refreshSession` ("Bad token scope") — rotation needs
  the main password.
- `cred_audit.py` PLATFORM_CRED_KEYS now includes `X_WEB_CT0`,
  `X_WEB_COOKIES_JSON`, `BSKY_*` — previously diverged silently because they
  weren't in the audit inventory.
