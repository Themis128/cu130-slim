---
name: publish-ops
description: >-
  Operate the SocialAuto publish pipeline: FOR UPDATE SKIP LOCKED queue debugging, Slack-digest alert triage (per-platform error signatures), pre-publish checks + maintenance windows, and adaptive rate limiting/backoff. Use for stuck/failed publishes or digest alerts.
---

# Publish Ops

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Publish queue operations | `publish-ops` |
| Publish alert triage | `publish-ops` → `publish-alert-triage/` |
| Publish preflight & maintenance window | `publish-ops` |
| Adaptive Rate Limiting | `publish-ops` |

## Publish queue operations

`publish_queue` rows are claimed by `process_publish_queue`
(`app/worker/tasks/publishing.py`, Celery beat every ~30s on the
`social-worker-publishing` queue).

### Claim semantics (post-fix, Sept 2026)

- Claims use `SELECT ... FOR UPDATE SKIP LOCKED` — concurrent workers take
  disjoint rows; two workers can never publish the same target.
- The claim commits before external API calls; `locked_by` holds the
  worker identity.
- Rows stuck in `processing` (killed worker, mid-task restart) are
  reclaimed after ~15 min by the stale-lock sweep — or force it with
  `scripts/publish_queue.py unstick`.
- `attempts < max_attempts` (default 3) auto-retries on failure.

### Lifecycle

- `publish_queue.status`: `pending` → `processing` → `completed`/`failed`
- `post_targets.status`: `pending` → `published`/`failed`/`skipped`,
  plus `platform_post_id`, `platform_url`, `error_message`
- Telegram/WhatsApp targets are `skipped` by design (messaging channels,
  no feed posts).

### Ops tool

```bash
python3 scripts/publish_queue.py list [--status failed] [--limit 20]
python3 scripts/publish_queue.py targets <post_id>   # per-platform status
python3 scripts/publish_queue.py dupes <post_id>     # queue + target rows
python3 scripts/publish_queue.py reset <queue_id>    # failed -> pending (attempts=0)
python3 scripts/publish_queue.py unstick [--minutes 15]  # reclaim stale processing
```

Or via the API: `POST /api/v1/publishing/queue/{id}/retry` (admin token).

### Duplicate reconciliation (after a race)

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

### Common failure signatures

| Error | Meaning |
|---|---|
| `402 credits depleted` (x.com) | X API write credits exhausted — browser fallback engaged; see `browser-ops` |
| `Browser bridge error 409 ... busy` | Another platform holds the browser busy-hold; retried automatically next cycle |
| `Browser busy with <p> session` | Same as above |
| `401` on tweets | OAuth2 token stale — auto-refresh attempted once at publish time |
| stuck `processing` | worker was restarted mid-task — `unstick` or wait 15 min |

## Publish alert triage

### When to use

- Slack digest posts "We couldn't publish a post" / "Analytics sync had trouble"
- Failed targets accumulate in `post_targets` / `publish_queue`
- Deciding what's retryable vs a platform limit vs a real bug

### Tool

```bash
## Full triage report (default 7d window)
.devin/skills/publish-ops/publish-alert-triage/scripts/alert_triage.py

## Narrow the window / sections
alert_triage.py --days 2
alert_triage.py --section failures,analytics
alert_triage.py --json
```

Sections: **failures** (post_targets status=failed grouped by signature),
**analytics** (snapshot error notes — the digest's "Heads up" source),
**accounts** (token status/expiry/refresh-token presence), **sessions**
(sidecar + bridge session health), **queue** (publish_queue status +
overdue items), **env** (config presence).

Classification classes: `app-bug` → fix code · `config` → env/console ·
`session` → reconnect/login · `platform-limit` → don't retry · `transient`
→ retry covers it · `info` → benign.

### Signature runbook

#### Platform limits — do NOT retry, adjust targeting/config

| Signature | Meaning | Action |
|---|---|---|
| `(#200) If posting to a group…` / `publish_to_groups` | FB Groups API deprecated (v19+) | Soft-skip; retarget to a Page (`pages_manage_posts`). Groups API removed Apr 2024 — reconnect will not help |
| `Instagram requires at least one image` | Text-only post to IG | Attach media or drop the IG target |
| `X free tier monthly write quota` | 1,500 tweets/mo exhausted | Waits for billing reset; browser fallback covers real posts |
| `stats HTTP 402` / `non_public_metrics` | Paid-tier metric on free plan | Expected — public_metrics still sync |
| `quota_exhausted` | X free-tier read cap hit (402/429 → coded state) | Expected — persists until billing reset; publishing unaffected |

#### Config — one-time fixes

| Signature | Meaning | Action |
|---|---|---|
| `url_ownership_unverified` | TikTok `PULL_FROM_URL` needs a verified domain | `tiktok-console-ops` → `domain-verify.py` (console token → CF DNS TXT → Verify) |
| `MEDIA_PUBLIC_BASE_URL not set` | No public media URL for TikTok pull | Set env to public https URL (CF tunnel); verify tunnel running |
| `(#10) Application does not have permission` | Meta app lacks scope/review | `developer-apps-ops` skill — `instagram_content_publish`/`pages_*` scopes |

#### Session — reconnect/re-login

| Signature | Meaning | Action |
|---|---|---|
| `Session has expired` / `Cannot parse access token` / `stats HTTP 401` / `REVOKED_ACCESS_TOKEN` | Dead OAuth token | Reconnect in Accounts UI; verify `refresh_expiring_tokens` rotates what it can. Note: X tokens live 2h (`expires_in:7200`) and refresh tokens are single-use — a skipped boundary run leaves ~1h of 401s; `_skip_for_recent_update` in token_refresh.py guards this |
| `Browser session not logged in` | Bridge/sidecar session dead | noVNC re-login or `session-ops` skill (cookie move) |
| `does not exist, cannot be loaded due to missing permissions` | Stale media id or missing scope | Usually media deleted on platform or wrong account — check |
| `publish_cancelled` (TikTok) | MEDIA_UPLOAD inbox draft dismissed | Republish; DIRECT_POST needs approved app audit |

#### App bugs — fix in code

| Signature | Root cause | Fix |
|---|---|---|
| `metric[N] must be one of…` | Meta rejects a metric name for this API version | `_meta_insights_get` adaptive dropper should eat these — if the note persists, the failing call bypasses the helper or the *last* metric was invalid |
| `(#100) The value must be a valid insights metric` | FB post-insights metric invalid | Valid post metrics: `post_impressions`, `post_media_view`, `post_clicks`, `post_reactions_like_total`, `post_comments`, `post_shares` |
| `Media ID is not available` | `media_publish` before container `FINISHED` | Poll `GET {container_id}?fields=status_code` until `FINISHED` before publishing |
| `Post button disabled` / `Could not find the tweet composer` / `Continue button not found` | X web UI selector drift | Update bridge X selectors (login flow + composer) |

#### Transient / info — no action

- `Session already active for X` / `Browser busy` — shared bridge serializes sessions; queue retries handle it.
- `Page.goto…browser closed` / `All connection attempts failed` — browser restart/network blip.
- `HTTP 5xx`, `upload timeout` — platform-side.
- `stats HTTP 404` / `video_not_found` — media deleted on platform.
- `member_stats_not_implemented`, `organization_lifetime` — informational markers, not errors.
- Digest soft-skips: `activityids`, `stats_unavailable` (mirrors `slack_digest.py`).

### Known noise sources

- **E2E/test teams** (Calendar E2E, E2E Test User, Wonf…) — digests run per
  team; a team with 0 accounts emits "No social accounts connected" every
  morning. Teams with no accounts AND no posts in the window arguably
  shouldn't emit at all.
- **LinkedIn sidecar 429** — circuit auto-opens on redirect loops; clears
  via `POST :9225/session/clear-rate-limit`. Repeated openings = LinkedIn
  anti-bot pressure, not an app bug.
- **IG `Session has expired` bursts** — token expires ~60 days after issue;
  `instagram/cloudless.gr` has no refresh token so it can't self-heal —
  reconnect is the only fix.

### Related skills

`tiktok-console-ops` (domain verify, QR login), `session-ops`
(cookie moves), `session-ops` (sidecar sweep), `developer-apps-ops`
(Meta permissions), `social-accounts-manager` (token refresh), `n8n-cloudless`
(digest workflow).

## Publish preflight & maintenance window

Two ops tools that answer "will the scheduled posts actually publish?" and
"how do I hold the browser bridge for manual work?"

### publish_preflight.py — scheduled-post verification

```bash
python3 scripts/publish_preflight.py              # next 48h, readable report
python3 scripts/publish_preflight.py --hours 24   # narrower window
python3 scripts/publish_preflight.py --post <uuid>  # one post
python3 scripts/publish_preflight.py --json       # machine-readable
```

Per scheduled post it checks:

- every `post_target` resolves to an **active** `social_account`
- OAuth `token_expires_at` outlives `scheduled_at`, or a refresh token exists
  (the hourly `refresh_expiring_tokens` task self-heals those)
- every `media_id` resolves to a `media_assets` row with a `public_url` that
  returns HTTP 200 + image/video content type (PDF allowed for LinkedIn
  document posts only)
- Instagram rules: media must be JPEG-compatible, carousels 2–10 slides
- posts whose `scheduled_at` is already past → warned as possibly missed

Exit code 1 if any post FAILs — safe for CI-style gating.

#### Reading the output

- `account status=expired` — the OAuth token is dead (preflight verifies it,
  not just the flag). Reconnect via SocialAuto Accounts page (OAuth) or the
  platform's noVNC login, then re-run. **Instagram exception:** IG publishing
  uses the web-API session (`private_api_session_id`/`private_api_csrf_token`/
  `private_api_ds_user_id` in `meta_data`), not the OAuth token — the flag can
  be stale either way. If the bridge's IG session is live (feed loads, not the
  login form), refresh meta_data from it:

  1. `maintenance_window.py start` (or grab the bridge between pollers)
  2. `POST :9223/session/start {"platform":"instagram","force":true,
     "interactive":true}` then `POST /session/extract` (X-Platform: instagram)
  3. Update `social_accounts.meta_data`: `private_api_session_id` =
     `encrypt_field(sessionid)` (encrypted), `private_api_csrf_token` =
     csrftoken **plaintext** (publisher reads it raw), `private_api_ds_user_id`
     = ds_user_id plaintext; set `status='active'`
  4. `POST /session/stop`, `maintenance_window.py stop`, re-run preflight

  Verify `ds_user_id` from the extract matches the account before writing —
  a mismatched session posts to the wrong profile. instagrapi login is not a
  fallback: it hits the "version out of date" wall.
- `token expires before schedule, no refresh token` — reconnect before the
  slot or the publish will fail.
- `URL unreachable` — R2/storage link is broken; regenerate or re-attach media.
- `no media attached` — text-only post; the strategy report flags these.

### maintenance_window.py — bridge contention control

```bash
scripts/maintenance_window.py start   # pause poller fleet (beat + 4 workers)
scripts/maintenance_window.py stop    # resume everything
scripts/maintenance_window.py status  # paused containers + bridge owner
```

Use `start` before manual Campaign Manager / profile edits through the
bridge (:9223) or a sidecar — otherwise scheduled pollers rotate in and
hijack the session mid-edit (409 busy loops). social-api stays up; only the
task fleet pauses. **Always `stop` afterwards** — a paused fleet silently
skips scheduled posts.

If the bridge still reports busy right after `start`, an in-flight poller
holds the busy-hold — wait ~30s and retry, or `POST /session/start` with
`{"platform": "<p>", "force": true}` for true emergencies.

### Related

- `scripts/session_health.py` — token/bridge/sidecar health matrix
- `scripts/publish_queue.py` — queue row inspection and stuck-lock recovery
- `scripts/session_transplant.py` — move a live session between browsers
- `.devin/skills/publish-queue-ops` — queue lifecycle and error signatures
- `.devin/skills/post-media-correctness` — media rules the preflight enforces

## Adaptive Rate Limiting

Patterns for reliable social media API request throttling, backoff, and proxy management.
Based on research from instagrapi best practices, auto_connector, and ProxyRotator.

### Core Principles

1. **Stable proxy identity**: One stable proxy/IP per account. Match country, locale,
   device settings, and saved sessions. Never rotate proxy mid-session.
2. **Honor Retry-After**: When a platform returns 429 with a `Retry-After` header, wait
   exactly that long before the next request. Don't guess — use the platform's hint.
3. **Exponential backoff with jitter**: On transient failures, wait `base * 2^attempt +
   random_jitter`. Jitter prevents thundering herd when multiple workers retry simultaneously.
4. **Per-worker pacing**: Each Celery worker should pace its own requests. Don't rely on
   a global rate limiter alone — workers may be on different machines.
5. **Two-layer cache**: In-memory hot layer (for the current request) + Redis TTL layer
   (for cross-worker sharing). Cache platform responses to avoid redundant API calls.

### SocialAuto Implementation

SocialAuto already has:
- Cloudflare WARP proxy (`warp-proxy:1080`) as the default SOCKS5 proxy
- Per-account proxy assignment via `INSTAGRAM_PROXY` env var
- Redis for cross-worker coordination
- Celery beat for scheduled tasks
- D1 circuit breaker for database writes

#### Recommended Additions

##### 1. Retry-After Header Handling

```python
## In app/services/publishing.py or a new app/services/rate_limiter.py
import asyncio
import random
from datetime import datetime, UTC

async def with_retry_after(
    func,
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 300.0,
    **kwargs,
):
    """Call a platform API function with Retry-After-aware backoff."""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            # Check for Retry-After header in the exception
            retry_after = _extract_retry_after(exc)
            if retry_after:
                delay = min(retry_after, max_delay)
            else:
                jitter = random.uniform(0, 0.5)
                delay = min(base_delay * (2 ** attempt) + jitter, max_delay)
            await asyncio.sleep(delay)

def _extract_retry_after(exc) -> float | None:
    """Extract Retry-After value from an HTTP exception."""
    if hasattr(exc, 'response') and exc.response is not None:
        headers = getattr(exc.response, 'headers', {})
        retry_after = headers.get('Retry-After') or headers.get('retry-after')
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    return None
```

##### 2. Per-Account Request Pacing

```python
## In app/services/rate_limiter.py
import redis.asyncio as redis
from app.core.config import settings

class PerAccountRateLimiter:
    """Redis-based per-account rate limiter for platform API calls."""

    def __init__(self):
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            self._redis = redis.from_url(settings.REDIS_URL)
        return self._redis

    async def acquire(self, account_id: str, platform: str, max_per_hour: int = 100):
        """Block until it's safe to make a request for this account."""
        r = await self._get_redis()
        key = f"rate:{platform}:{account_id}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 3600)  # 1 hour window
        if count > max_per_hour:
            ttl = await r.ttl(key)
            if ttl > 0:
                await asyncio.sleep(ttl)
            await r.delete(key)

    async def cooldown(self, account_id: str, platform: str, seconds: int):
        """Set a cooldown period for an account (e.g., after a 429)."""
        r = await self._get_redis()
        key = f"cooldown:{platform}:{account_id}"
        await r.setex(key, seconds, "1")
```

##### 3. Platform-Specific Rate Limits

| Platform | Endpoint | Limit | Window |
|----------|----------|-------|--------|
| LinkedIn | Share API | 150 posts | per day |
| LinkedIn | Marketing API | 100K calls | per day |
| Twitter/X | v2 API | 50 posts | per 24h |
| Twitter/X | v2 API | 300 reads | per 15 min |
| Instagram | Graph API | 25 posts | per 24h |
| Instagram | Graph API | 200 calls | per hour |
| Threads | Publishing | 25 posts | per 24h |
| TikTok | Content API | 6 videos | per 24h |
| Facebook | Graph API | 200 calls | per hour |

### Usage in Workers

```python
## In app/worker/tasks/publishing.py
from app.services.rate_limiter import PerAccountRateLimiter

rate_limiter = PerAccountRateLimiter()

async def publish_to_platform(account, post):
    # Wait for rate limit clearance
    await rate_limiter.acquire(account.id, account.platform, max_per_hour=50)

    try:
        result = await with_retry_after(
            platform_publish_func,
            account=account,
            post=post,
        )
    except RateLimitError:
        # Set a cooldown and requeue
        await rate_limiter.cooldown(account.id, account.platform, seconds=3600)
        raise RetryException(countdown=3600)
```

### Free/Open-Source Tools Referenced

- **instagrapi**: https://github.com/subzeroid/instagrapi (MIT) — Instagram private API with
  rate limiting best practices
- **ProxyRotator**: https://github.com/keyhankamyar/ProxyRotator — Async V2ray proxy rotation
- **auto_connector**: https://github.com/ivasik-k7/auto_connector — Adaptive throttling with
  Retry-After handling
- **Cloudflare WARP**: Free SOCKS5 proxy, already integrated as `warp-proxy` container
