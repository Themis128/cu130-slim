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
