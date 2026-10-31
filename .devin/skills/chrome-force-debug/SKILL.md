---
name: chrome-force-debug
description: The single-instance fix for :9222 — chrome-force-debug.bat
  hard-kills every chrome.exe (retry loop), verifies none remain, then
  launches the debug profile with the CDP port so the flags actually bind.
  The prior launch raced a running instance. Tool: scripts/force_debug.py —
  `status` probes :9222, `kill` force-kills chrome, `flag` drops the
  watcher's launch trigger pointing at the force .bat.
---
# Chrome force-debug — single-instance-safe :9222 bind
