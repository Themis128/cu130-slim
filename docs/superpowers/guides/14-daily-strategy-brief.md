# Daily strategy brief

SocialAuto emails a strategy brief every morning. It is rendered by the
`daily_strategy_brief` papermill notebook (code path:
`strategy_report.build_strategy_report` as fallback) and scheduled by Celery
beat (`daily-strategy-report`, `30 10 * * *` — 10:30 Athens).

## What the brief contains

| Section | Contents |
|---|---|
| **Platform pulse (30 days)** | Per-platform posts, impressions, engagement, avg ER, 7-day momentum, benchmark comparison, best posting window, confidence |
| **Recent posts** | Latest published items per platform (failed/pending targets are excluded) |
| **Content pillars (30d)** | Posts per pillar (Proof / Testing / Perspective), `starved` flags, and a "post from X next" nudge |
| **Tomorrow's playbook** | LLM-drafted next-day actions |
| **Growth initiatives** | Tracked experiments and their status |
| **Data gaps** | Sync errors, disconnected accounts, platform API limits |

## Messaging channels in the brief

WhatsApp Business, Telegram bots, and Messenger-enabled Facebook Pages are
**connected messaging channels** — they have no posts, impressions, or
engagement metrics, so they cannot appear in the platform pulse table.

Instead the brief ends the pulse with:

```
ℹ messaging (connected, no post metrics) — telegram bot, whatsapp business, messenger (Cloudless.gr page)
```

A Facebook Page counts as a messaging channel when Messenger is set up on it
(`meta_data.messenger_setup.subscribed = true`, written by
`POST /api/v1/messenger/{account_id}/setup`). The page still appears in the
pulse normally — the note only flags its additional messaging role.

If a connected messaging account does not appear in this line, check that the
account row is `status='active'` in `social_accounts`.

## Trigger manually

Run the same task the beat entry schedules — the notebook render (with
code-path fallback):

```bash
docker exec -i social-worker-default celery -A app.worker.celery_app call \
  app.worker.tasks.notebook_reports.run_notebook_report \
  --kwargs='{"notebook":"daily_strategy_brief","parameters":{"insight_days":30},"send":true,"fallback_to_code":true}'
```

To exercise only the code-path fallback:

```bash
docker exec -i social-worker-default celery -A app.worker.celery_app call \
  app.worker.tasks.digest.send_daily_strategy_report
```

## Verify the schedule

```bash
docker exec -i social-worker-default celery -A app.worker.celery_app inspect scheduled
docker logs celery-beat | grep daily-strategy-report
```
