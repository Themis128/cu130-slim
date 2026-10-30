---
name: cdp-watch-ops
description: Watch for the debug Chrome binding :9222 (consent-root-ops
  chrome-debug-launch.bat) and auto-drive the pending human gates the moment
  it comes up — reads the LinkedIn verification code from the gmail tab,
  fills the checkpoint, completes the OAuth consent. Tool: scripts/cdp_watch.py
  — --once checks now, no-args watches 24h.
---

# CDP watch

`cdp_watch.py` waits for :9222 → drives the LinkedIn gmail-code + consent.
The one trigger is the user's `chrome-debug-launch.bat` double-click.
