# debug-launch-ops — bind :9222 where agent launches can't

Edge/Chrome won't bind `--remote-debugging-port` when spawned from a
non-interactive context (SSH agent, remote exec, service) — the flag is
swallowed and the port never listens. It needs a **desktop-session
launch**. This skill wires the escape hatch that works.

## Paths (in order)

1. **`ChromeDebug` scheduled task** — registered to run in the logged-in
   user's session (not "whether logged on or not"). `schtasks /run` on it
   spawns Edge interactively → port binds. Auto-rebinds at every login.
   `scripts/debug_launch.py --register` creates it; `--run` triggers it.

2. **Desktop double-click** — `C:\tmp\chrome-debug-launch.bat` (written
   by `--write-bat`); one click when nothing else works.

3. **`gate_runner2.py`** — full clone→launch→forwarder→watch chain.

## Verify

    python3 scripts/debug_launch.py --status   # is :9222 listening?

## Traps encoded

- Agent-spawned `start`/`Start-Process`/`schtasks /run` on a non-
  interactive task NEVER bind the port — no error, just silent 404.
- LenovoVantage WebView2 steals :9222 — kill `msedgewebview2.exe` first.
- Chrome/Edge ≥136 refuses debug on the real default profile — always
  use the clone (`C:\tmp\edge-full` / `chrome-full`).
