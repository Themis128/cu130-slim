# gov-portal-watch

Config-driven watcher for authenticated Greek government portals. One engine
(`scripts/engine.py`) serves any portal: reload page over CDP → extract rows
via JS → diff vs state file → Telegram alert on change; optional GSIS/TaxisNet
auto re-login when the auth marker disappears.

First config: `scripts/watches/idika.json` (IDIKA Epidoma Paidiou 2026).
The live deployment on the OFFICE host is `C:\Users\tbaltzakis\idika-watch\`
(`portal.json` + thin `watch.py`/`ping.py`/`relogin.py` wrappers + `engine.py`)
driven by schtasks `IdikaWatch` (hourly), `IdikaKeepalive` (20 min),
`IdikaMorningRelogin` (daily 09:00).

## Config shape (`watches/<name>.json`)

| field | meaning |
|---|---|
| `name` | alert prefix |
| `url` | portal page URL (also the CDP tab matcher — host substring) |
| `auth_regex` | regex tested against the extracted rows JSON; match = logged in |
| `extract_js` | JS IIFE returning `JSON.stringify(rows)` or `{rows:[...]}` |
| `login` | `"gsis"` enables auto re-login, anything else disables |
| `creds` | path (relative to config dir) to `{username,password,amka?}` — keep OUT of repo |
| `host` | optional; host substring for tab match + login-return check (default: from `url`) |
| `login_url` | optional; portal login page for the GSIS chain (default: `url`) |
| `oauth_button` | optional; element id of the "Συνέχεια στο TaxisNet" button (default `Gsis.OAuth2`) |
| `auth_expr` | optional; JS returning `'auth'`/`'guest'` for the GSIS login verify step (default: IDIKA's) |
| `chat_id` | optional, default `7017840434` |
| `state_file` | optional, default `<name>-state.json` beside config |

## Usage

```
python scripts/engine.py scripts/watches/idika.json            # one check
python scripts/engine.py scripts/watches/idika.json ping       # keepalive reload
python scripts/engine.py scripts/watches/idika.json relogin    # proactive re-auth
python scripts/watch.py [config.json]                          # wrapper (default portal.json)
python scripts/run-all-watches.py [config.json ...]            # check every watches/*.json
python scripts/doctor.py [--tg]                                # health check all deployments
```

Silent = no change. Prints `baseline`/`changed`. `doctor.py` verifies secrets,
CDP, tabs, live auth state, writable state files, and (with `--tg`) Telegram
delivery — exit 0 = healthy.

## Adding a portal

1. Copy `watches/idika.json`, adjust `url`/`auth_regex`/`extract_js`.
2. If it uses TaxisNet: `"login":"gsis"` + a creds file (see gsis-login-ops).
3. Point a schtasks task at `watch.py <config>` (wrap in .bat — MSYS mangles
   `/create`; see gmail-telegram-watch SKILL.md gotchas).

## CDP notes

- Lane `:9229`, profile `C:\Users\tbaltzakis\chrome-debug-profile`.
- `websocket.create_connection(..., suppress_origin=True)` — Chrome rejects
  WS handshakes carrying an Origin header.
- Always `Page.reload` + wait `Page.loadEventFired` before evaluating;
  mid-navigation evals return undefined.
- `ensure_browser()` relaunches the debug Chrome if the port is down.
- Auth check MUST be DOM-based (auth_regex on extracted content) — logged-out
  gov pages often keep the same URL with an empty body.
