# idika-watch

Watches the Greek IDIKA "Epidoma Paidiou" (child benefit) portal for application
status changes and auto re-logs-in when the session expires. Alerts via Telegram
(@Cloudless_newbot).

## How it works

- Chrome debug instance: `chrome-debug-profile` on CDP `:9229` (lane per
  global rules — `:9222` belongs to the user's normal Chrome).
- `watch.py` (one-shot, run hourly by schtasks `IdikaWatch`): reloads
  `https://www.idika.gr/epidomapaidiou/2026/LandingPage`, extracts the
  "Aitiseis" table via `Runtime.evaluate`, diffs against `state.json`.
- Auth check = presence of `Κωδικός` (applications table header). The
  logged-out LandingPage keeps the same URL with an empty table — never
  rely on the URL alone.
- On logged-out state → `login.ensure_login()` performs the full chain:
  IDIKA login page → "Συνέχεια στο TaxisNet" → GSIS OAuth
  (`oauth2.gsis.gr`, fills `j_username`/`j_password` only if
  `login.jsp` appears — a live GSIS session skips it) → consent page
  `Αποστολή`/`#btn-submit` → IDIKA `LoginAMKA` page (fill `#AMKA`, submit)
  → verify table header.
- Telegram chat id: `7017840434`; bot token in
  `%APPDATA%/devin/secrets/tg-bot-token`.

## Credentials

`%APPDATA%/devin/secrets/taxisnet.json`:
`{"username": "<AFM>", "password": "<taxisnet pw>", "amka": "<AMKA>"}`

If GSIS demands SMS/2FA or a captcha, auto-login fails → one Telegram
"please log in manually" alert (throttled via `login_alert` flag).

## Files

- Canonical copy: `.devin/skills/idika-watch/` in cu130-slim (mirrored to
  `.cursor/skills/idika-watch/`)
- Live copy actually run by schtasks: `C:\Users\tbaltzakis\idika-watch\`
  (`watch.py`, `login.py`, `ping.py`, `relogin.py`, `state.json`)

## Ops

- Manual run: `python watch.py` (prints `baseline`/`changed`, silent = no change)
- Remove task: `schtasks /delete /tn IdikaWatch /f`

## Scheduled tasks

- `IdikaWatch` — hourly, `watch.py` (diff + auto re-login on expiry)
- `IdikaKeepalive` — every 20 min, `ping.py` (reloads LandingPage so
  ASP.NET/GSIS server-side sessions don't idle out; all auth cookies are
  browser-session cookies, so **never close the debug Chrome window** —
  closing it kills every session and forces full re-auth next run)
- `IdikaMorningRelogin` — daily 09:00, `relogin.py` (proactive session
  check/repair at a sane hour so a 2FA/captcha challenge surfaces while
  the user is awake, not at 3 AM)

## Chrome autostart topology

At-logon scheduled tasks are denied on this box, but the user's Startup
folder already has `cdp-launch.bat` (headless chrome `:9330` on the same
`chrome-debug-profile` + `cdp_relay.py` exposing `0.0.0.0:9229`) and
`start-chrome-debug.bat` (headed, `:9222`). Either way CDP ends up on
`:9229`. Caveat: if the headless+relay variant wins the profile lock at
logon there is no visible window for a manual 2FA fallback — close it
and `ensure_browser()` (called by both `watch.py` and `ping.py`) relaunches
a headed instance on `:9229`.

## Avoiding manual re-login

GSIS oAuth2.0 (non-OTP) uses TAXISnet credentials only — there is no
inherent 2FA on this client (`LK4B7J43558`); the OTP variant is a separate
grant IDIKA doesn't use. Residual risks and mitigations:

- Browser closed → all cookies are session-scoped → full re-auth. Keep
  the `chrome-debug-profile` Chrome running; `watch.py` auto-launches it.
- Server-side idle timeout → mitigated by `IdikaKeepalive` (20 min).
- GSIS risk-based challenge on the password form → cannot be automated;
  surfaces via Telegram, user logs in once in the debug window, GSIS
  remembers the device/profile afterwards.
- Saving TAXISnet creds in the debug profile's Chrome password manager
  makes even the manual fallback one click.
- Debug Chrome must be running; `watch.py` auto-launches it on
  `chrome-debug-profile` if the `:9229` endpoint is down.
- CDP websocket needs `suppress_origin=True` (Chrome rejects WS with an
  Origin header unless `--remote-allow-origins` is set).
- `Page.reload` + wait for `Page.loadEventFired` before evaluating —
  evaluating mid-navigation returns undefined.
