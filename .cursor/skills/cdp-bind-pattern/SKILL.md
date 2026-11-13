---
name: cdp-bind-pattern
description: The complete :9222 CDP-bind pattern for the SocialAuto stack —
  the problem (headless chrome binds 127.0.0.1, ignores 0.0.0.0; WSL can't
  reach it; interactive-session CDP can't launch from WSL), and the working
  chain (userspace relay -> headless chrome -> flag-watcher -> cdp_watch ->
  consent-root). The full diagnosis + every launcher + the auto-heal hook.
---
# CDP-bind pattern — :9222 end-to-end

## The problem chain

1. **Headless chrome binds `127.0.0.1`** — `--remote-debugging-address=0.0.0.0`
   is silently ignored (Chrome bug, GoogleChromeLabs/chrome-for-testing#203).
2. **WSL can't reach Windows `127.0.0.1`** — separate network namespace; the
   Windows host IP (`$(ip route | awk '{print $3}')`) is the only path, and it
   needs the port bound on a Windows interface.
3. **Interactive CDP can't launch from WSL** — a scheduled/background chrome
   launch can't bind the user-session port; the WSL->Windows interop
   (`powershell.exe`, `explorer.exe`, `cmd.exe`) is session-degraded and dies
   under `UtilAcceptVsock` errors.
4. **`netsh portproxy` needs admin** — the obvious fix (forward 0.0.0.0:9222 ->
   127.0.0.1:9223) requires elevation; a SYSTEM task can't be created without
   admin either.

## The working solution — userspace relay

`cdp-relay.py` — a **userspace TCP forwarder** (no admin) listening on
`0.0.0.0:9222` and proxying to headless chrome's `127.0.0.1:9223`:

    WSL -> Windows-host-IP:9222 -> relay -> 127.0.0.1:9223 -> headless chrome

`cdp-bind-user.bat` starts the relay + launches headless chrome on `:9223`
(`--headless=new --remote-debugging-port=9223`). The relay's the admin-free
answer to the loopback-bind problem.

## The launch chain

    .devin/triggers/launch-chrome.flag
      -> chrome-debug-watch.ps1 (watcher, Run-key installed, every 30s)
      -> cdp-bind-user.bat (relay + headless chrome)
      -> :9222 bound (via relay on host IP)
      -> cdp_watch (social-worker beat) detects it
      -> consent-root-ops drives the live session
      -> consent gates / Tailscale-ACL / omv-ha chain auto-complete

## Files

| File | Role |
|---|---|
| `chrome-debug-launch.bat` | GUI launch (interactive session) |
| `chrome-force-debug.bat` | single-instance-safe kill+launch |
| `chrome-headless-debug.bat` | headless CDP (binds 127.0.0.1) |
| `cdp-bind-user.bat` | relay + headless chrome (no admin) |
| `cdp-relay.py` | userspace 0.0.0.0:9222 -> 127.0.0.1:9223 forwarder |
| `chrome-debug-watch.ps1` | flag->bat watcher (Run-key) |
| `bind_9222.py` / `force_debug.py` | status / schedule / install probes |

## The one human step

The launch needs a **visible Windows action** — double-click
`cdp-bind-user.bat`, or sign-in (the Run-key fires the watcher). The relay
removes the admin dependency; the interactive launch is the irreducible step.

## 2026-11-13 addendum — :9229 debug profile + WSL reachability

- The **consent-root debug Chrome** runs on **:9229** (`chrome-debug-profile`,
  user's real logged-in sessions: GitHub Themis128, dash.cloudflare.com via
  GitHub SSO). Port 9222 is claimed by Lenovo Vantage WebView2 — do not use.
- **WSL cannot reach it**: chrome binds Windows `127.0.0.1:9229`; WSL
  `localhost`/host-IP both fail, and a userspace relay on a new port
  (e.g. `0.0.0.0:9230 -> 127.0.0.1:9229`) is blocked by Windows Firewall
  for WSL→host traffic (adding a rule needs admin).
- **Working pattern**: run CDP scripts with **Windows python**
  (`python C:\...`) — `websocket-client` is installed there; copy scripts
  to `C:\Users\tbaltzakis\` (or mirror the repo path layout — scripts that
  resolve `.env` via `parents[N]` need the same depth, e.g.
  `C:\Users\tbaltzakis\lirepo\.devin\skills\<skill>\scripts\`).
  `curl.exe http://localhost:9229/json/list` also works from WSL for
  read-only tab listing.
- `member_dp_probe.py` / `li_session_heal.py` accept any CDP base in
  `CDP_CANDIDATES` — point them at `http://localhost:9229` when run on
  Windows; on WSL they need the relay chain above (currently down).
- Relaunch command (no taskkill — preserves the user's main Chrome):
  `Start-Process chrome.exe -ArgumentList '--remote-debugging-port=9229','--user-data-dir=C:\Users\tbaltzakis\chrome-debug-profile','about:blank'`
  WARNING: `chrome-debug-launch.bat` / the `CloudlessDebugChrome` task run
  `taskkill /F /IM chrome.exe` — kills ALL Chrome including the user's
  session. Prefer the Start-Process form.

## 2026-11-13 evening — relay retarget to the live :9222 chrome

Actual state found: the visible `chrome-debug-profile` Chrome runs on
**:9222** (loopback), NOT :9229. The canonical relay
`C:\Users\tbaltzakis\devin-scripts\cdp_relay.py` (pythonw, bound
`0.0.0.0:9229`) still targeted the dead headless lane `127.0.0.1:9330`
→ every WSL connection got RST. Fix applied: `TARGET` edited to
`("127.0.0.1", 9222)`, relay restarted → **`http://172.22.144.1:9229`
now serves the live debug Chrome to WSL** (firewall rule
`CDP-Relay-9229` already allows TCP 9229 inbound).

- If the relay dies, restart: `Start-Process pythonw
  C:\Users\tbaltzakis\devin-scripts\cdp_relay.py` — do NOT run
  `cdp-launch.bat` while the visible chrome holds the profile (headless
  :9330 can't share `chrome-debug-profile`).
- Websocket clients MUST pass `suppress_origin=True` (websocket-client)
  or Chrome 137+ rejects the handshake 403 — no
  `--remote-allow-origins` flag on this instance.
- SO_REUSEADDR on Windows lets a second relay bind :9229 alongside the
  canonical one — connections split unpredictably. Always check
  `Get-NetTCPConnection -LocalPort 9229` for the owning PID before
  starting another relay.
- **LinkedIn itself is the current blocker, not CDP**: the debug chrome
  gets HTTP 429 on linkedin.com (account restriction cooldown) — cookie
  heal reports `cookies-rejected`, credential login hits the Cloudflare
  challenge. Automation is armed (`li_session_heal` cron */30min +
  `li_portal_sidecar` */30min); it self-recovers when the 429 lifts.
