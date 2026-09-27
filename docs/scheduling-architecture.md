# Scheduling Architecture

Workstation topology/ports: [`CODEMAP.md`](CODEMAP.md) (incl. § Workstation OFFICE/WSL).

How a Post goes from "scheduled" to "published" — the beat sweeps, the
publish_queue, retries, token refresh, and per-platform publish modes.

## Lifecycle

```
Post.status:  draft ──(scheduled_at set)──▶ scheduled ──▶ publishing ──▶ published
                                     │                            └──▶ failed / partial
                                     ▼
PostTarget.status (per account): pending ──▶ published | failed | skipped

PublishQueue.status: pending ──▶ processing ──▶ completed | failed | cancelled
```

Creating a post with `scheduled_at` sets `PostStatus.SCHEDULED` and one
`PostTarget(status=pending)` per target account (`api/content.py`). Clearing
`scheduled_at` on a scheduled post drops it back to `draft`.

## Celery beat sweeps (`app/worker/celery_app.py`)

| Task | Cadence | Queue | Role |
|---|---|---|---|
| `check_scheduled_posts` | every 60s | default | Finds `scheduled` posts with `scheduled_at <= now`; creates a `publish_queue` row per target; post → `publishing`. Posts with no targets → `failed` immediately. |
| `process_publish_queue` | every 30s | publishing | Claims due `PENDING` rows and publishes. |
| `refresh_expiring_tokens` | hourly at :15 | publishing | Refreshes OAuth tokens expiring within 4h (`token_refresh.py`). |
| `cleanup-publish-queue` | periodic | — | Purges terminal queue rows older than 3 days. |

## Queue mechanics (`app/worker/tasks/publishing.py`)

- `publish_queue` row: `post_id`, `social_account_id`, `scheduled_at`,
  `priority`, `status`, `attempts`/`max_attempts`, `locked_at`/`locked_by`.
- Claim is atomic: `SELECT … FOR UPDATE SKIP LOCKED` batch of 50 ordered by
  `priority DESC, scheduled_at ASC` — concurrent processors get disjoint rows.
- `PROCESSING` rows stale >15 min are reclaimed (crash recovery).
- On success → queue `COMPLETED`, target `published` (+`platform_post_id`,
  `platform_url`, `published_at`), `platform_specific` merged with returned
  platform meta. Per-target webhook + author email fire immediately; the
  Slack success post is aggregated per post (see Notifications below).
- On soft-skip (`pub.skipped`) → `COMPLETED` + target `skipped` — reserved
  for deliberate bypasses (platform quota/cap, deprecated API surface). Hard
  failures such as a wrong-account X session are NOT skipped; they retry and
  alert like any other error.
- On failure → `attempts += 1`; at `max_attempts` the row is `FAILED`, target
  `failed` with `error_message`, failure notification sent.
- Post text passes `auto_correct` (LanguageTool) right before publish.

### Idempotency & dedupe (migration `c8d9e0f1a2b3`)

Duplicate external posts used to slip through when a queue row was retried
after a crash, a stale lock was reclaimed, or the same `(post, account)` was
enqueued twice. Three layers now prevent that:

- **DB constraint** — partial unique index `ux_publish_queue_active_target`
  on `(post_id, social_account_id) WHERE status IN ('pending','processing')`.
  A second active row for the same pair is rejected outright; terminal rows
  (completed/failed/cancelled) don't collide, so re-queues stay possible.
  The migration collapses pre-existing dupes — keeps a `processing` row over
  a `pending` one (cancelling a running row can't stop its external call,
  but a retained pending row could publish again), ties broken by
  `created_at`.
- **API** — `POST /publishing/queue` returns `409` for an existing active
  row (pre-check + `IntegrityError` fallback for the check/insert race).
- **Worker** — before calling the platform, each claim re-checks its
  `post_target`: already `published` → the queue row completes without an
  external call. Same-batch duplicate rows collapse to the first.

## Per-platform publish behavior

| Platform | Scheduled behavior |
|---|---|
| LinkedIn | `.pdf` media → `create_document_post` (native carousel). Images → multi-image post. Org + member targets both supported. |
| Threads / Facebook / Instagram | Public `image_url` fetch — media must be reachable (see media-creation-architecture → Serving). |
| TikTok | `platform_specific.tiktok.publish_mode`: `MEDIA_UPLOAD` (default — lands in the TikTok app inbox for manual publish; works pre-audit) or `DIRECT_POST` (auto-publishes; requires app audit approval for public posts + `privacy_level` from `creator_info.privacy_level_options`). Photo posts always use `PULL_FROM_URL` → media URL must be on a TikTok-verified domain. When no public post URL exists (inbox draft / missing id), `platform_url` falls back to `tiktok.com/@<username>`. |
| Twitter/X | OAuth2 user context + OAuth1 media upload; publish-time token refresh on 401/403 (`_refresh_oauth2_token`). On quota (402/credits-depleted) the browser bridge fallback runs — but only after an **identity check**: the logged-in handle (profile nav link, fallback: account-switcher label) must match `social_account.username`. A mismatch or an undetectable handle is a **hard failure** (retries → alert), never a silent skip and never a post under a foreign account. |
| Facebook | A Graph/sidecar response with no `post_id` is treated as failure — the target is never marked `published` with an empty external id. |

## Token refresh interplay

Scheduled posts depend on tokens staying alive:

- `refresh_expiring_tokens` (hourly :15) refreshes any account with
  `token_expires_at <= now + 4h`, or any account already marked `expired`.
- A recent `updated_at` skips re-refresh — **unless the token is already
  expired** (never starve a dead token).
- Instagram Business Login tokens self-refresh via `ig_refresh_token` (no
  stored refresh token).
- TikTok access tokens refresh via `tiktok_client.refresh_token`; the hourly
  sweep keeps them alive as long as the refresh token is valid.
- X/Twitter refresh tokens are single-use — the rotated refresh token is
  persisted on every refresh.

## Failure visibility

Failures surface via `_notify_publish_failure` (Slack `#socialauto` + the
daily digest email) and `post_targets.error_message`. Failure alerts include
the post's other outcomes — `published: linkedin, threads · failed:
instagram` — so a partially published post doesn't read as fully dead.
The daily digest (`send_daily_slack_digest`, 09:00 Europe/Athens) summarizes
failed/queued posts — see `publish-alert-triage` skill for the
error-signature runbook.

## Notifications

One aggregated Slack message per post goes to `#socialauto-publishing`
(`SLACK_PUBLISHING_WEBHOOK_URL`) — content preview (≤140 chars,
mrkdwn-escaped so post copy can't inject `<!channel>`/`<@U>` mentions) plus
one `• *platform*: url` line per published target. This replaced the old
fan-out of one message per platform.

`_maybe_send_publish_summary` fires it **after a post has no active queue
rows left**, so targets that land in different batches or on different
workers are still covered by a single all-platform message. Delivery is
claimed atomically (`posts.platform_specific.publish_summary_claim`, a
hash of the published-target set): retries and concurrent finishers cannot
double-post, and publishing to a *new* account later produces a new claim
key and notifies again. Per-target integrations (webhook, author email)
still fire per platform.

## What "accepts scheduling" requires per platform

1. `posts.status=scheduled` + `post_targets.pending` — the sweep does the rest.
2. A live OAuth token at fire time — hourly refresh handles expiry.
3. Media fetchable by the platform — public `media/view` URL on a
   platform-verified domain (TikTok), real JPEG for Instagram, PDF for
   LinkedIn carousels.
4. TikTok only: `publish_mode=DIRECT_POST` + valid `privacy_level` for fully
   automatic publish; otherwise it schedules into the app inbox.
