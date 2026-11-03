---
name: chrome-launch-ops
description: Launch the :9222 debug Chrome when WSL→Windows interop is
  broken (cmd.exe/powershell.exe vsock fails). Tries every launch path —
  schtasks, explorer, rundll32, wt.exe, powershell — then falls back to a
  file-drop trigger (`.devin/triggers/launch-chrome.flag`) that the
  Windows-side watcher picks up. Tool: scripts/chrome_launch.py — `fire`,
  `status`, `arm-watcher` (installs a 1-min scheduled task on Windows that
  launches on the flag).
---
# chrome-launch-ops
Every path to bind :9222 — including the file-drop fallback for broken interop.
