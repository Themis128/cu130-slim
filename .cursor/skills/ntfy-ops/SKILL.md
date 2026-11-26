# ntfy-ops

Server-side operations for the self-hosted ntfy (k3s `ntfy` namespace on
`github-omv`, NodePort `80:30080`, `auth_default_access: deny-all`). Pairs
with **ntfy-phone-ops** (device side).

## When to use

- A publisher's alerts aren't arriving (URL/auth/ACL problem)
- Granting a topic to a user, rotating a password, minting a publish token
- Auditing which services publish to ntfy and whether their path works
- Checking topic health end-to-end (publish → reachable subscribers)

## Tool

`scripts/ntfy_ops.py` — talks to the pod over SSH (`ssh github-omv`, kubectl
exec) and to the HTTP API for verification:

```bash
python ntfy_ops.py users                 # list users + ACLs
python ntfy_ops.py grant phone cloudless-alerts rw
python ntfy_ops.py passwd phone          # rotate pw (NTFY_PASSWORD env)
python ntfy_ops.py token cloudless-app   # mint non-expiring Bearer token
python ntfy_ops.py publish nextcloud-ops "test" --token tk_...
python ntfy_ops.py audit                 # scan repo for ntfy URLs + check each
```

## Traps (learned 2026-10-29)

- **`ntfy.cloudless.gr` is Cloudflare-Access gated** — interactive SSO a
  machine client can never answer; every `*.cloudless.gr` tunnel hostname is
  too. Publishers MUST use `http://<tailnet-or-lan>:30080` + auth, never the
  public hostname.
- `deny-all` means a topic needs BOTH a granted user AND credentials —
  "Connection error"/"Login required" = ACL, not network.
- `ntfy user change-pass` takes `NTFY_PASSWORD` env, NOT `--password`
  (hangs on stdin otherwise).
- Non-expiring publish tokens: `ntfy token add <user>` → `tk_…`;
  send as `Authorization: Bearer tk_…`.

## Alert fan-out (2026-11-12)

Every severity-routed alert now ALSO publishes to `cloudless-alerts` on
`https://ntfy.cloudless.gr` — the phone's ntfy app is the third always-on
lane alongside Telegram + Slack. Wired at the two choke points:

- `tg_alert.py send()` → `_ntfy_publish()` (all ~28 home-ops watchers)
- `slack_notifications.send_telegram_alert()` + `post_alert_to_slack()`
  + `post_digest_text_to_slack()` → `_post_ntfy_alert()` (backend Celery)

Severity → ntfy `Priority` header: critical=5, ops=4, intel=3, digest=2.
Publisher identity: ntfy user `alert-relay` — **write-only** on
`cloudless-alerts`, `nextcloud-ops`, `cloudless-ops`; never-expiring
Bearer token lives in repo `.env` as `NTFY_ALERT_TOKEN`
(+ `NTFY_ALERT_BASE_URL`, optional `NTFY_ALERT_TOPIC` override).

Corrections to the 2026-10-29 traps (verified live 2026-11-12):

- `ntfy.cloudless.gr` is **NOT Access-gated for API/subscription
  traffic** — machine publishes (Bearer) and the Android app's
  websocket both work over the public hostname. The earlier "must use
  tailnet :30080" guidance is obsolete for this hostname.
- **Cloudflare 403s bare python User-Agents** (urllib `Python-urllib`,
  httpx default) — every publisher must send a browser-ish UA header.

## Archive reader (2026-11-19)

`archive-reader` — dedicated **read-only** ntfy user for the
`notify_archive_ingest` collector (incident-resolution-ops). Grants:
`cloudless-ops`, `cloudless-alerts`, `nextcloud-ops` — all `ro`.
Never-expiring Bearer token lives in `.env`/`socialauto-standalone/.env`
as `NTFY_READER_TOKEN`. Mint/rotate:

```bash
python ntfy_ops.py users                       # verify grants
NTFY_PASSWORD=<rand> ... ntfy user add archive-reader
python ntfy_ops.py grant archive-reader <topic> ro
python ntfy_ops.py token archive-reader        # → tk_… into .env
```

The `alert-relay` publisher stays write-only by design — do not grant it
read. The cloudless.gr workflow `ntfy-reader-token.yml` automates the
same mint over the omv runner for rotation without local SSH.

## MCP server (added 2026-11-26)

`scripts/ntfy-mcp-server.py` — stdlib-only stdio MCP server, registered as
`ntfy` in `.devin/mcp_config.json`. 8 tools: ntfy_health, ntfy_publish,
ntfy_read, ntfy_users, ntfy_grant, ntfy_token, ntfy_passwd,
ntfy_app_setup_help. Read/publish auth: `NTFY_TOKEN` bearer wins, else basic
auth `NTFY_USER`/`NTFY_PASSWORD` (falls back to repo .env `NTFY_PC_PASSWORD`).
New trap burned in: **Cloudflare blocks `Python-urllib/*` UA with HTTP
403/1010** — the server always sets a custom UA; any future urllib client
must too.

## Windows desktop app lane (verified 2026-11-26)

- The ntfy web/desktop app stores subscriptions in **browser-local storage —
  there is no server-side subscription registry**. No API/MCP can
  pre-populate the app; subscription is an inherently client-side act.
- The `pc` user (rw on the 4 ops topics) was created for the PC app; password
  in repo .env `NTFY_PC_PASSWORD`.
- GUI-drive scripts live on the workstation `C:\Users\tbaltzakis\devin-scripts\`
  (`subpass.ps1`, `subnext.ps1`, `screencap.ps1`, `uiclick.ps1`): raise +
  maximize window, click sidebar "Subscribe to topic", SendKeys topic, click
  SUBSCRIBE. Coordinates are FIXED PIXELS for the maximized window on the
  1920x1080 workstation display (sidebar Y depends on subscribed-topic
  count) — workstation-specific reference scripts, re-measure on any other
  display before use.
- **Synthetic input cannot steal focus** from a window the user is typing
  in — drive only when the app window is foregrounded and hands-off, or the
  keystrokes leak into whatever has focus (happened live: text landed in the
  Devin input box).
- Chrome shows a "Save password?" popup after the app login — dismiss, the
  app caches its own session.
