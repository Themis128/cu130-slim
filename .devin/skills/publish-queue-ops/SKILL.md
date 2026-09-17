---
name: publish-queue-ops
description: Inspect, debug, and repair the SocialAuto publish queue. Covers the atomic FOR UPDATE SKIP LOCKED claim in app/worker/tasks/publishing.py, queue/post_targets lifecycle states, stuck 'processing' row recovery after worker restarts, retrying failed items, and reconciling duplicate external posts after queue races. Use when posts publish twice, queue rows are stuck, a platform target stays pending/failed, or verifying per-platform publish status.
---

# Publish queue operations

`publish_queue` rows are claimed by `process_publish_queue`
(`app/worker/tasks/publishing.py`, Celery beat every ~30s on the
`social-worker-publishing` queue).

## Claim semantics (post-fix, Sept 2026)

- Claims use `SELECT ... FOR UPDATE SKIP LOCKED` — concurrent workers take
  disjoint rows; two workers can never publish the same target.
- The claim commits before external API calls; `locked_by` holds the
  worker identity.
- Rows stuck in `processing` (killed worker, mid-task restart) are
  reclaimed after ~15 min by the stale-lock sweep — or force it with
  `scripts/publish_queue.py unstick`.
- `attempts < max_attempts` (default 3) auto-retries on failure.

## Lifecycle

- `publish_queue.status`: `pending` → `processing` → `completed`/`failed`
- `post_targets.status`: `pending` → `published`/`failed`/`skipped`,
  plus `platform_post_id`, `platform_url`, `error_message`
- Telegram/WhatsApp targets are `skipped` by design (messaging channels,
  no feed posts).

## Ops tool

```bash
python3 scripts/publish_queue.py list [--status failed] [--limit 20]
python3 scripts/publish_queue.py targets <post_id>   # per-platform status
python3 scripts/publish_queue.py dupes <post_id>     # queue + target rows
python3 scripts/publish_queue.py reset <queue_id>    # failed -> pending (attempts=0)
python3 scripts/publish_queue.py unstick [--minutes 15]  # reclaim stale processing
```

Or via the API: `POST /api/v1/publishing/queue/{id}/retry` (admin token).

## Duplicate reconciliation (after a race)

If duplicates reach external platforms (pre-fix race), use the dedupe
tool — **dry-run by default**, deletion needs `--delete --yes`:

```bash
python3 scripts/dedupe_posts.py list <post_id>
python3 scripts/dedupe_posts.py delete <platform_post_id> --platform threads --delete --yes
```

Platform delete support:

- **Threads** — `ThreadsAPI.delete_post(media_id)` — supported
- **Facebook Page** — `FacebookAPI.delete_post(post_id)` — supported
- **LinkedIn** — `LinkedInAPI.delete_post(post_urn)` — supported
- **Twitter/X** — API delete will likely `402 credits depleted`; use the
  browser session instead
- **Instagram** — Graph API has **no media delete**; delete in the app
- **TikTok** — no API delete; delete in the app

Keep the post whose `platform_post_id` is recorded in `post_targets` —
that row is the tracked one. The tool marks deleted rows
`status='deleted'` after a successful external delete.

## Common failure signatures

| Error | Meaning |
|---|---|
| `402 credits depleted` (x.com) | X API write credits exhausted — browser fallback engaged; see `twitter-browser-ops` |
| `Browser bridge error 409 ... busy` | Another platform holds the browser busy-hold; retried automatically next cycle |
| `Browser busy with <p> session` | Same as above |
| `401` on tweets | OAuth2 token stale — auto-refresh attempted once at publish time |
| stuck `processing` | worker was restarted mid-task — `unstick` or wait 15 min |
