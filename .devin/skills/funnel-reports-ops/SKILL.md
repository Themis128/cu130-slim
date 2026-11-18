---
name: funnel-reports-ops
description: Operate the SocialAuto funnel report suite — daily lead/inbox pulse, UTM→booking attribution digest, weekly content scoreboard, monthly funnel economics, plus the 6h unified-inbox snapshot feed. Use when a funnel report is missing/wrong, when adding a new report, when checking what the reports actually measure, or when attribution/inbox data looks stale.
---

# Funnel reports ops

The decision-report layer on top of the daily ops batch. Built 2026-11-18
after the reporting-gap review (daily = operational scan, weekly = content
allocation, monthly = economics; every section ends in an action line).

## Report inventory

| Report | Beat | Task | Email subject |
|---|---|---|---|
| Lead & inbox pulse | daily 10:30 Athens | `app.worker.tasks.funnel_reports.run_lead_pulse` | `[Cloudless] Lead pulse — YYYY-MM-DD` |
| Attribution digest | daily 11:00 | `run_attribution_digest` | `[Cloudless] Attribution digest — …` |
| Content scoreboard | Mon 08:30 | `run_content_weekly` | `[Cloudless] Content scoreboard — …` |
| Funnel economics | 1st 09:00 | `run_funnel_monthly` | `[Cloudless] Funnel economics — …` |
| Inbox snapshot (no email) | every 6h :15 | `app.worker.tasks.inbox_snapshot.run_inbox_snapshot` | — |

All four email reports dedupe per Athens day via Redis keys
`funnelreport:sent:{key}:{date}` (20h TTL) — a watcher resend cannot
double-mail. Builders live in `app/services/funnel_reports.py`; the
`FunnelReport`/`_send`/`BUILDERS` contract is the extension point for new
reports.

## Data sources each report reads

- **lead_pulse** — `leads` (SocialAuto), EspoCRM `Lead`/`Opportunity`
  (`espocrm_client.list_all`), live unified-inbox capture
  (`inbox_snapshot.capture` → also persists a snapshot batch),
  `whatsapp_frt_stats` (first-response median/p90/breaches over
  `whatsapp_messages`), `web_analytics_events` consultation visits by
  `payload->>utm_source`.
- **attribution** — `web_analytics_events` page_view + consultation path
  grouped by utm_source/referrer host, `booking_created` events (written
  by the calcom webhook from booking metadata), SocialAuto + EspoCRM
  leads, opportunities by stage.
- **content_weekly** — latest `post_analytics_snapshots` per post in the
  last 7d vs prior 7d (min 50 impressions), per-platform totals,
  `follower_snapshots` deltas.
- **funnel_monthly** — EspoCRM leads/opps by stage, Polar `/orders`
  (amounts are cents → /100), `ad_daily_metrics` LinkedIn spend.

## The attribution chain (how a booking gets its source)

1. `AttributionCapture` (locale layout) stores first-touch UTM/referrer in
   sessionStorage (`cl_attribution`) — cloudless.gr repo.
2. `/api/calendar/book` + `/api/agent/book` read it via
   `getStoredAttribution()`, sanitize with `parseAttributionField`, and
   pass snake_case `utm_*` keys as Cal.com booking `metadata`
   (`attributionToCalMetadata` in both routes).
3. Cal.com webhook → `app/api/calcom_webhook.py::_record_booking_attribution`
   writes a `booking_created` `web_analytics_events` row (domain
   `cloudless.gr` config) with the utm payload + hashed attendee email.
4. The attribution digest groups `booking_created` by `utm_source`.
   EspoCRM lead/deal descriptions also carry an `Attribution:` line
   written site-side — CRM truth does not depend on the webhook.

Known limit: attribution is first-touch within the browser session
(sessionStorage) — a visitor who returns days later on another device
shows as direct/unattributed. Per-session stitching beyond that needs a
persistent visitor id (GA4 client_id is the candidate).

## Inbox snapshot semantics

- `inbox_snapshots` rows are per-conversation, one `batch_id` per capture.
- `unread` comes from each provider (Graph `unread_count`, bridge flags);
  Instagram DMs report `unread=False` always — IG coverage is
  thread-count only until the API exposes read state.
- FRT stats are WhatsApp-only (the only channel with message-level
  persistence). Other channels would need per-message storage first.
- SLA tiers in `inbox_snapshot.py`: 4h target, 24h hard breach — adjust
  `FRT_TARGET_SECONDS`/`FRT_HARD_SECONDS` there, not in the report.

## Ops

```bash
# status table (no resend/alert)
python3 .devin/skills/report-delivery-ops/scripts/report_delivery_watch.py --status

# force a resend pass for missing reports
python3 .devin/skills/report-delivery-ops/scripts/report_delivery_watch.py --resend

# run one report builder live (inside social-api — has DB + secrets)
docker exec social-api python -c "
import asyncio
from app.services.funnel_reports import build_lead_pulse
print(asyncio.run(build_lead_pulse()).to_text())"

# queue a report task manually
docker exec social-worker-default python -c "
from app.worker.celery_app import celery_app
print(celery_app.send_task('app.worker.tasks.funnel_reports.run_attribution_digest', kwargs={'send': True}).id)"

# inbox snapshot on demand
docker exec social-worker-default python -c "
from app.worker.celery_app import celery_app
print(celery_app.send_task('app.worker.tasks.inbox_snapshot.run_inbox_snapshot').id)"
```

## Failure modes seen

- **Builder exception → no email, no alert**: the task dies inside
  `run_async`; check `docker logs social-worker-default | grep funnel_reports`.
  All external calls inside builders are wrapped — a total failure means a
  code bug, check the traceback first.
- **EspoCRM `(query failed)`**: `ESPOCRM_BASE_URL`/`ESPOCRM_API_KEY` empty
  in the container env → section prints the failure marker, report still
  sends. Verify with `docker exec social-api env | grep ESPOCRM`.
- **Inbox section "no conversations / all fetches failed"**: provider
  tokens or browser bridge down — the report still sends; check
  `docker logs social-api | grep 'Inbox fetch error'`.
- **`booking_created` count = 0**: either no bookings (check EspoCRM
  opportunities) or the site deploy predates the metadata passthrough —
  attribution only flows for bookings made after the cloudless.gr PR
  deploys.
- **Duplicate emails**: should be impossible (Redis day-lock); if seen,
  check the lock key `funnelreport:sent:*` in Redis db 0 and worker clock
  vs Athens timezone.

## Adding a new funnel report

1. `async def build_<key>() -> FunnelReport` in `funnel_reports.py`,
   register in `BUILDERS`.
2. `_make_task(...)` entry in `app/worker/tasks/funnel_reports.py`.
3. Beat entry in `celery_app.py` + hour/minute settings in `config.py`.
4. Watcher spec in `report_delivery_watch.py` (kind=count, correct
   `due_hour`, resend task name; callable `expected` for non-daily).
5. Verify: run the builder in social-api, queue the task, confirm the
   email lands via `report_delivery_watch.py --status`.
