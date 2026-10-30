---
name: win-trigger-ops
description: Register a Windows scheduled task that auto-fires
  chrome-debug-launch.bat on logon so :9222 binds without a manual
  double-click — removes the last physical gate. Tool:
  scripts/register_trigger.ps1 (run once on Windows, admin not needed for
  own-user AtLogOn).
---

# Win trigger

`register_trigger.ps1` → `CloudlessDebugChrome` AtLogOn task → the debug
Chrome + :9222 come up automatically on each sign-in — cdp_watch + every
CDP-gated heal fires without the manual bat.
