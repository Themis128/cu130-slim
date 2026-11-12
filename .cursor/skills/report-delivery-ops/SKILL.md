# report-delivery-ops

Detect and repair **missing** scheduled report emails to
`tbaltzakis@cloudless.gr` — the complement to `report-watch.py`
(home-ops), which alerts on failures *inside* reports that did arrive.

## Why this exists

2026-11-12: the daily batch partially vanished — the n8n
`socialauto-daily-slack-digest` workflow died on an `onError` node
(`List IntraVox Pages`, fixed same day) and the strategy-brief / ops-health
notebook emails never landed. Nothing alerted because every existing
watcher only inspects mail that *exists*.

## What it checks

`scripts/report_delivery_watch.py` reads the last 10 days of INBOX over
IMAP (omv-ha dovecot, creds `MAIL_TBALTZAKIS_PASSWORD` /
`MAILBOX_PASSWORD` in `cu130-slim/.env`) and matches subjects against the
`REPORTS` registry, which mirrors the live beat schedule in
`social-automation/backend/app/worker/celery_app.py`:

| key | subject pattern | cadence |
|---|---|---|
| `socialauto_daily` | `your SocialAuto daily` | daily + Mon weekly + 1st monthly rollup (count-based) |
| `strategy_brief` | `[SocialAuto] Strategy brief` | daily ~09:00 |
| `ops_health` | `[SocialAuto] Ops health` | daily ~09:15 |
| `monetization` | `SocialAuto monetization report` | daily |
| `promo_scan` | `Free-promo scan` | daily |
| `linkedin_ads` | `LinkedIn ad report` | daily 10:00 |
| `omv_health` | `[omv]` / pending-config / HEARTBEAT | freshness ≤3d |

When a report is missing past its `due_hour` the script **auto-resends
once per day** via `celery_app.send_task(...)` inside
`social-worker-default`, then TG-alerts `#ops` only if it's still missing
on a later pass (state: `~/.local/state/report-delivery-watch/state.json`,
deduped per spec per day, 14-day prune).

## Usage

```bash
python3 .devin/skills/report-delivery-ops/scripts/report_delivery_watch.py --status   # table only
python3 .devin/skills/report-delivery-ops/scripts/report_delivery_watch.py           # check + resend + alert
python3 .devin/skills/report-delivery-ops/scripts/report_delivery_watch.py --resend   # force resend pass
```

Cron (user crontab, host is EET): `45 10,14,20 * * *` — after the ~10:15
morning batch, midday stragglers, and an evening final pass.

## Gotchas

- `send_daily_slack_digest` is normally triggered by the **n8n** workflow,
  not beat (double-post guard). The resend path calls the celery task
  directly — it produces the same email; only use it when the email is
  actually missing.
- Notebook reports (`run_notebook_report`) take several minutes
  (papermill) — a resend-queued status is not proof of delivery; the next
  cron pass confirms.
- If a resend keeps failing, check `docker logs social-worker-default`
  for the task and the email provider path (`EMAIL_PROVIDER`, SMTP) —
  a send-side outage makes every report "missing".
- Keep `REPORTS` in sync with `celery_app.py` beat entries and the n8n
  daily-digest workflow; stale entries cause false MISSING alerts.
