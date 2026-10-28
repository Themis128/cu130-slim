# Analytics digests — daily, weekly, and monthly reports

SocialAuto emails and posts a performance digest to Slack on three
cadences. All three share the same builder (`slack_digest.build_digest`)
— what changes is the window and the extra sections that unlock at
larger windows.

## What each digest contains

| Section | Daily (1d) | Weekly (7d) | Monthly (30d) |
|---|---|---|---|
| Overview — posts, published, scheduled, drafts, failed | ✓ | ✓ | ✓ |
| Impressions / engagement / connected accounts | ✓ | ✓ | ✓ |
| Top posts (organic only — ad rows excluded) | ✓ | ✓ | ✓ |
| Warnings — sync errors, disconnected accounts | ✓ | ✓ | ✓ |
| **Growth** — per-account follower delta + rate | — | ✓ | ✓ |
| **Growth extras** — non-follower reach %, paid/organic adds, best posting window | — | ✓ | ✓ |
| **MoM deltas** — this window's growth vs prior window | — | — | ✓ |
| **Forecast** — naive linear next-month follower count | — | — | ✓ |
| **Funnel** — impressions → engagement → clicks, ER% per platform | — | — | ✓ |

## Schedule

| Digest | Celery beat name | Fires | Channels |
|---|---|---|---|
| Daily | `daily-slack-digest` | every day, `SLACK_DIGEST_HOUR` | Slack |
| Weekly | `weekly-slack-digest` | Monday, `SLACK_DIGEST_HOUR` | Slack + email |
| Monthly | `monthly-slack-rollup` | 1st of month, `SLACK_DIGEST_HOUR` | Slack + email |

`SLACK_DIGEST_HOUR` (default 9) is the UTC hour. Monthly fires on the
1st and reports on the trailing 30 days.

## Trigger one manually

```bash
# Preview (no posts, returns the rendered report JSON):
curl "http://localhost:8083/api/v1/ops/daily-digest/preview?days=30&team_id=<TEAM>"

# Send for real (Slack + email):
curl -X POST "http://localhost:8083/api/v1/ops/daily-digest?days=7&post_to_slack=true&post_to_email=true"
```

`days=30` turns on the monthly sections for any manual run too.

## Where the data comes from

- **Follower deltas** — `follower_snapshots` rows written by
  `analytics_sync`. Baseline is the first non-zero reading inside the
  window; prior-window rows only feed the MoM comparison. Per account
  (not per platform) — a LinkedIn org page and personal profile are
  separate rows.
- **Funnel** — per-post `post_analytics_snapshots` deltas (max − min
  with `greatest(…, 0)` so counter resets can't go negative),
  `linkedin_ads` rows excluded.
- **Non-follower reach / paid adds / activity** — `analytics_events`
  (`audience_reach_split`, `follower_attribution`, `audience_activity`)
  written by `analytics_sync`. `audience_activity` requires ≥100 IG
  followers (Meta `online_followers` limitation).
- **MoM** — needs `follower_snapshots` history covering 2× the window.
  On the first monthly run after enabling it, `prev_delta`/`forecast`
  are absent until a prior-window baseline exists.

## Troubleshooting

- **Monthly email didn't arrive on the 1st** — check
  `celery-beat` is running and `monthly-slack-rollup` appears in
  `celery -A app.worker.celery_app inspect scheduled` / beat logs.
- **Growth shows +N that looks too big** — check for a metric-flip
  outlier in `follower_snapshots` (the write site rejects >5× jumps;
  older bad rows age out of the window on their own).
- **No MoM numbers** — expected until one full prior window of
  snapshots exists (30 days for the monthly digest).
