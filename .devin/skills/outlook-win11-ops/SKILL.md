# outlook-win11-ops — new Outlook for Windows (olk) diagnostics + MS365 outage watch

Operate the **new Outlook for Windows** app (`olk.exe`, package
`Microsoft.OutlookForWindows`) on the Windows 11 host from WSL. Created after the
2026-11-13 triage: app looked "broken" (launch → ~30 s → exit, blank "Outlook"
window) but the real cause was a **global Microsoft 365 outage** — package was
healthy, auth worked, the service was down.

## Golden rules learned the hard way

- **Check Microsoft status before touching the app.** Run
  `scripts/ms365_outlook_watch.py` first. During the 2026-11-12/13 outage the
  signatures were: `outlook.office.com/mail/` → **417**, `arc.msn.com/v4/` →
  **500**, OWA SSL timeouts. A stalled OOBE/mail load with a healthy package =
  service-side, not local.
- **Never `Get-AppxPackage -Name` with an array** — it throws
  `Cannot convert 'System.Object[]' to 'System.String'`. Query one name per call.
- **Do not reinstall during a service outage.** `Reset-AppxPackage` gives the
  same clean state with zero downside; a Store reinstall lands on the same
  degraded endpoints.
- **olk process ≠ broken.** It is a thin native host; the real UI is WebView2
  under `%LOCALAPPDATA%\Microsoft\Olk\EBWebView`. olk at ~60–75 MB with 6
  `msedgewebview2.exe` children whose `--user-data-dir` ends in `Microsoft\Olk`
  is NORMAL.
- **Page state lives in the WebView2 History DB**, not the window title:
  copy `%LOCALAPPDATA%\Microsoft\Olk\EBWebView\Default\History` and read
  `urls` — `/mail/oobe` = stuck in setup, `/mail/inbox/id/...` = working,
  title "Mail - <name> - Outlook" = fully loaded. Yesterday-evening inbox hits
  vs today's bare "Outlook" titles was the fastest outage tell.
- **App logs**: `%LOCALAPPDATA%\Microsoft\Olk\logs\olk_*.log` (UTC timestamps,
  tab-separated). Grep for `oobeFunnelEvent` (OOBE stage), `GetToken` result
  codes, `DeactivatedApp` (window focus flapping — benign), `Application
  exiting` (clean shutdown). OneAuth `errorCode 2002 / HTTP 401` lines are
  benign optional-Graph-call noise.
- **Account auto-provisioning**: Windows WAM accounts appear via
  `AddIdentity ... addedToMap true` — `baltzakis.themis@gmail.com` was added
  with zero clicks because it's the Windows sign-in account.

## UI automation limits (verified 2026-11-13)

- **UIA sees 0 descendants inside the WebView2 content** — cannot drive
  in-app forms via UIAutomationClient.
- **`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333` is
  IGNORED** by olk's embedded WebView2 (verified: lands in package env via
  `Invoke-CommandInDesktopPackage`, but the browser process cmdline never gets
  the flag and nothing listens on 9333). Don't retry this path.
- Windows **Settings → Email & accounts** on this build only offers
  Microsoft/work-or-school — no Google/IMAP lane.
- Practical account-adding = user clicks in-app, or blind SendKeys + log
  verification (fragile; prefer the human for OAuth/2FA anyway).

## Recovery watcher

`scripts/ms365_outlook_watch.py` — probes the three degraded endpoints, writes
`~/.local/state/ms365-outlook-watch.json`, fires **TG #ops** on the
down→up transition. `--wait` blocks (5-min polls, ~8 h) for session use.

```bash
# one-shot
python3 .devin/skills/outlook-win11-ops/scripts/ms365_outlook_watch.py
# blocking watch
python3 .devin/skills/outlook-win11-ops/scripts/ms365_outlook_watch.py --wait
# cron (user): */15 * * * * python3 <repo>/.devin/skills/outlook-win11-ops/scripts/ms365_outlook_watch.py >> ~/logs/ms365-outlook-watch.log 2>&1
```

## Diagnostics

`scripts/outlook_diag.ps1` — one-shot read-only health dump (package, process +
WebView2 tree, crash events, data footprint, latest log funnel lines,
reachability probes). Run from WSL:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File \
  /mnt/c/Users/tbaltzakis/cu130-slim/.devin/skills/outlook-win11-ops/scripts/outlook_diag.ps1
```

(Mirror copy lives in `.cursor/skills/outlook-win11-ops/scripts/`.)

## Account-add runbook (post-recovery)

Canonical params: `C:\Users\tbaltzakis\AppData\Local\Temp\outlook_account_params.txt`
(regenerate from this skill if lost — values below are non-secret).

| Account | Type | Parameters | Gate |
|---|---|---|---|
| baltzakis.themis@gmail.com | Google/MSA | already attached via WAM | none — auto |
| tbaltzakis@cloudless.gr | IMAP (own dovecot) | `192.168.1.130:993` SSL, SMTP `192.168.1.130:587` STARTTLS, user=full address, pass=`MAIL_TBALTZAKIS_PASSWORD` in `cu130-slim/.env` | creds verified live 2026-11-13 (IMAP login OK, 3989 msgs, SMTP AUTH OK). NOTE: `mail.cloudless.gr` public IP does NOT accept IMAP/SMTP from this LAN — use `192.168.1.130` |
| themis.baltzakis@gmail.com | Google | in-app Google sign-in | likely human 2FA — only `GMAIL_REFRESH_TOKEN_THEMIS_BALTZAKIS` (API token) exists, no sign-in password stored |

In-app path once service is healthy: Outlook → Settings → Accounts → Add
account → enter email → for IMAP choose "Advanced options" and enter the
server fields above.

## Verification checklist after changes

1. `ms365_outlook_watch.py` shows healthy (or note the residual degradation).
2. `outlook_diag.ps1` — package `Ok`, olk + 6 webview children, no new
   Application Error/1000/1001 events.
3. History DB newest `urls` row reaches `/mail/inbox/...` with title
   `Mail - Themistoklis Baltzakis - Outlook` (not bare `Outlook`).
4. `olk_*.log` shows `GetToken ... result 1` and no repeated `Application
   exiting` within seconds of launch.
