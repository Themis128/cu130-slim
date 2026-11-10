# x-recovery-ops — unattended X/Twitter account recovery

End-to-end lane for the case where every stored X credential is dead
(OAuth refresh token invalid, `browser_cookies.auth_token` dead,
`TWITTER_LOGIN_*` absent) — the exact state hit 2026-11-10.

Drives the **legitimate X password-reset flow** on a dedicated Windows
headless Chrome, self-reads the confirmation code from Gmail, sets a new
password, completes the OAuth2 authorize click, and lands fresh tokens —
no human step unless X demands a factor we don't have.

## Architecture

`` unhasimp
Windows probe Chrome (headless, CDP 127.0.0.1:9444, profile chrome-xprobe)
        │  x_daemon.py  — reset state machine, 25-min rate-limit retries
        │  x_finalize.py — Storage.getCookies → auth_token/ct0
        ▼
C:\Users\tbaltzakis\{x_daemon_status.json, x_cookies.json, x_new_password.txt, x_oauth_url.txt}
        ▲
WSL x_finalize_watch.py — polls status → runs finalize → writes .env
        (X_WEB_AUTH_TOKEN, X_WEB_CT0, TWITTER_LOGIN_USERNAME/PASSWORD)
        → compose up -d social-api social-worker-default celery-beat
        → polls social_accounts.twitter until status=active
```

## Deploy (one-time per host)

```bash
# Windows side (via powershell.exe from WSL):
cp scripts/x_daemon.py scripts/x_finalize.py  →  C:\Users\tbaltzakis\
# generate inputs once:
#   x_new_password.txt  — new X password (also becomes TWITTER_LOGIN_PASSWORD)
#   x_oauth_url.txt     — signed-state+PKCE authorize URL (see "OAuth URL" below)
# launch probe chrome once:
chrome.exe --headless=new --remote-debugging-port=9444 \
  --remote-allow-origins=* --user-data-dir=C:\Users\tbaltzakis\chrome-xprobe about:blank
# daemons:
python C:\Users\tbaltzakis\x_daemon.py            # hidden
# WSL side:
nohup python3 .devin/skills/x-recovery-ops/scripts/x_finalize_watch.py > x_finalize_watch.log 2>&1 &
```

## Hard-won gotchas (all verified live)

- **HeadlessChrome UA → X 403s the page.** `Network.setUserAgentOverride`
  with a real Chrome UA is mandatory before any navigation.
- **Arkose/FunCaptcha challenges are auto-solved when configured** —
  `arkose_info()` detects the enforcement iframe/pkey/blob, `capsolver_solve()`
  calls capsolver.com `FunCaptchaTaskProxyless` (~$0.001-0.003/solve),
  `inject_arkose_token()` tries the known token surfaces (hidden inputs,
  iframe postMessage, window callbacks). Needs `x_capsolver.json`
  `{"api_key": "..."}` in `C:\Users\tbaltzakis\`; without it the daemon
  alerts "human needed" on first detection instead of waiting for the
  3-strike unknown-screen alert. Injection is best-effort — untested
  against a live challenge; verify `x_daemon_log.txt` for `arkose inject:`.
- **x_supervisor.py** wraps the daemon: relaunches it 60s after any exit
  (timeout/crash/kill), so coverage is indefinite rather than the daemon's
  12h `X_MAX_SECONDS` window. Run both hidden; killing `x_daemon` is safe —
  the supervisor respawns it with the current on-disk code.
- **Phone/SMS checkpoints are automated** via `x_phone_bridge.py` —
  daemon writes `x_phone_needed.json` on `C:\Users\tbaltzakis\`, the WSL
  cron (`*/2min`, user crontab) reads the newest X/Twitter SMS through the
  adb content-provider lane (Galaxy S22+, proven in wa_verify.py) with a
  Termux-SSH fallback, and writes `x_phone_code.json` back. The daemon
  fills + submits. X-only sender filter — never falls back to unrelated
  SMS codes. Requires: phone on WiFi with USB/wireless ADB authorized
  (`adb devices` shows the device).
- **Telegram alerts** — `alert()` posts to `x_tg_alert.json`
  `{"token","chat"}` (ops channel); silent when absent. Fires on DONE,
  oauth failure, daemon timeout, Arkose detect/solve, and 3x unrecognized
  screens.
- **Synthetic `.click()` does NOT submit** X's onboarding SPA — the
  recovery-method button needs `Input.dispatchMouseEvent` trusted clicks
  (implemented in `PG.trusted_click_re`).
- **The confirmation code is NOT a "verification code" email** — it
  arrives inside the "Password reset request" mail body; parser reads
  the first 6-digit group in the first ~1500 chars.
- **The rate limit is account-level, not IP-level** — verified by
  rerunning through a WARP SOCKS5 exit (different IP, same block).
  Keep `X_RETRY_SLEEP` ≥ 1500s; hammering keeps the window alive.
- Docker Desktop vsock flaps kill the compose browsers — this lane
  deliberately lives on Windows Chrome + WSL watcher, outside Docker.
- `docker restart` does NOT reload `.env` — the watcher uses
  `docker compose up -d` to recreate.

## OAuth URL generation

`scripts/` has no generator — build it like `consent_healers._try_bridge_reauth`:
PKCE verifier/challenge + `sign_oauth_state({"t": team_id, "cv": cv})`
(HMAC-SHA256 with `JWT_SECRET_KEY`), scopes
`t tweet.read tweet.write users.read media.write offline.access dm.read
dm.write like.write like.read follows.write bookmark.read`, redirect
`TWITTER_REDIRECT_URI` (`https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback`).
The callback exchanges the code and stores tokens server-side — the
browser only needs to click Authorize.

## Status files (C:\Users\tbaltzakis\)

- `x_daemon_status.json` — phase: rate_limited|logged_in|oauth|done|oauth_stuck|timeout
- `x_finalize_status.json` — cookie extraction result
- `x_cookies.json` — extracted auth_token/ct0/… (sensitive, delete after use)
- `x_daemon_log*.txt`, `x_finalize_watch.log` — audit trail

## When it reports done

1. Verify `social_accounts.twitter` → `active` (watcher does this).
2. Optionally delete `x_cookies.json` / `x_new_password.txt`.
3. `TWITTER_LOGIN_*` + `X_WEB_*` in `.env` now feed
   `consent_healers._x_credentials_login` — future re-auths are unattended.
