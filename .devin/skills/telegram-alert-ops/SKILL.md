---
name: telegram-alert-ops
description: >
  Severity-routed Telegram alerting to private admin channels — the SRE
  doc pattern (route by severity+ownership, one critical fallback path,
  critical also lands in ops for a complete record). Four private
  channels: #critical 4389402878 (P1: consent lapse, api/bridge/DMR down),
  #ops 4429103353 (P2: token expiry, healer state changes, queue issues),
  #intel 4362141941 (P3: log_intel triage, datalake insights),
  #digest 4363123500 (P4: daily rollups). Tool: scripts/tg_alert.py —
  --status maps channels + bot state, --test sends to each, "<sev> <text>"
  one-shots. Runtime: send_telegram_alert(severity, text) in
  slack_notifications.py. Requires @Cloudless_newbot as admin on each
  channel (post permission) — Bot API can't post to non-member channels.
---

# Telegram Alert Ops

`tg_alert.py --status` — channel map + reachability
`tg_alert.py --test` — send a test to all 4
`tg_alert.py ops "msg"` — one-shot

Env overrides: `TG_CH_CRITICAL/OPS/INTEL/DIGEST`.
Router: `app/services/slack_notifications.py::send_telegram_alert`.
