---
name: wa-verify-ops
description: WhatsApp number verification — retries request_code until Meta's
  SMS throttle clears, then wa_verify + ADB reads the code off the phone.
  Tool: scripts/wa_code_watch.py --once (single attempt) or no-args (90-min
  retry loop). Meta gate: 'wait 1 hour' on back-to-back code requests.
---

# WhatsApp verify

`wa_code_watch.py` waits out Meta's SMS cooldown → requests the code →
`wa_verify` (human-gate-bridges) reads it off the ADB-connected phone.
