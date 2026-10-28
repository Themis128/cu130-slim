# X (Twitter) free web fallback — `x_web`

SocialAuto publishes to X through the **official X API v2** first. When the
developer account has no prepaid credits, X answers `HTTP 402
credits-depleted` (or `UsageCapExceeded` / quota-type 429) for posting *and*
reads. The `x_web` provider (`social-automation/backend/app/services/x_web.py`)
is a free fallback that talks to x.com's own web GraphQL API with the
browser session cookies of the account:

| Use | Library | Notes |
| --- | --- | --- |
| Posting (text, 1–4 images, GIF, video, threads) + primary reads | [tweety](https://github.com/mahrtayyab/tweety) (`tweety-ns`, MIT) | pinned to main commit `f6bfc8610bd3ec8af2fe000076bc1b53a6327e7d` (PyPI 2.4.1 is stale) |
| Secondary reads (user followers, user tweets, tweet details) | [twscrape](https://github.com/vladkens/twscrape) `0.20.1` (MIT) | read-only; telemetry disabled (`TWS_TELEMETRY=0`) |

It is **off by default** (`X_WEB_FALLBACK_ENABLED=false`).

## ⚠️ Risk — read before enabling

Automating x.com with session cookies is against the X Terms of Service.
X can challenge, lock or **suspend** the account (@TBaltzakis) at any time,
and web endpoints change without notice. The guards below reduce, not remove,
that risk. Only posting and read-only metrics are automated — **no likes,
follows, DMs or replies to others**. Keep the official API as the primary
path (add credits at console.x.com whenever possible).

## Getting the cookies (never a password)

`x_web` never logs in with a username/password. Export the cookies of a
normal, already-logged-in browser session:

1. Log in to <https://x.com> as the account in a normal desktop browser
   (Chrome/Edge/Firefox), completing any 2FA/challenge by hand.
2. Open DevTools (F12) → **Application** tab (Firefox: **Storage**) →
   **Cookies** → `https://x.com`.
3. Copy the **Value** of `auth_token` and of `ct0`.
4. Put them in the WSL checkout's `.env`
   (`/home/tbaltzakis/cu130-slim/.env`):

   ```dotenv
   X_WEB_AUTH_TOKEN=<auth_token value>
   X_WEB_CT0=<ct0 value>
   X_WEB_FALLBACK_ENABLED=true
   ```

5. Recreate the services so the env is re-read:
   `docker compose up -d social-api social-worker-publishing social-worker-default`.

Do not log out of that browser session (logging out invalidates
`auth_token`). If X rotates the session, repeat the export. Optionally
`X_WEB_COOKIES_JSON` can hold a full export (JSON object `{name: value}` or a
`[{"name": ..., "value": ...}]` list from a cookie-export extension);
`X_WEB_AUTH_TOKEN` / `X_WEB_CT0` override the same names in it.

## How selection works

**Publishing** (`_publish_twitter` in `app/services/publishing.py`):

1. Media is validated first: up to 4 images, **or** 1 GIF, **or** 1 video.
   Missing/empty files, unknown types (PDF, WEBM…), extension/content
   mismatches, >4 images or mixed sets fail the post (`permanent`, alerted)
   — a post is never published with missing or wrong media. Images are
   re-encoded to baseline RGB JPEG (≤5 MB).
2. Official API v2 (+ v1.1 media upload). A non-quota media-upload error fails
   the post instead of posting text-only.
3. On 402 / credits-depleted / usage cap (or for video, which the official
   path cannot upload) → `x_web` (tweety) when enabled and both cookies set.
   The logged-in handle must match the account (`@username`), else fail.
4. On a transient `x_web` error → the existing browser-bridge fallback (images
   only; it refuses media it cannot attach). Video never reaches the bridge.

**Analytics** (`sync_twitter_account` in `app/services/analytics_sync.py`,
run by `social-worker-default`): when the official endpoints return 402, the
sync reads account followers + the latest timeline page + details for up to
`X_WEB_ANALYTICS_MAX_TWEET_LOOKUPS` uncovered tweets via tweety, falling back
to twscrape. Snapshots are stored with `source=x_web_tweety|x_web_twscrape`.
When `x_web` is configured it replaces the browser-bridge timeline scrape.

## Safety guards

State lives in Redis (`x_web:guard`, shared by all workers); if Redis is
unreachable it falls back to `X_WEB_STATE_FILE` (default
`/app/uploads/.x_web_state.json`).

- **Circuit breaker** — HTTP 401/403/429, locked/suspended accounts,
  challenges/captcha/Arkose, "palm"/transaction-id errors, X codes
  32/64/88/89/141/185/215/226/326/344/353/399 trip it for
  `X_WEB_BREAKER_HOURS` (6h). The job fails immediately with a descriptive
  error, a Slack alert goes to the alerts channel (once per trip), and all
  x_web posting *and* reads stop until it expires.
- **Daily cap** — `X_WEB_MAX_POSTS_PER_DAY` (5) per rolling 24h.
- **Gap** — a random `X_WEB_MIN_GAP_MINUTES`–`X_WEB_MAX_GAP_MINUTES`
  (10–30 min) between posts. Cap/gap hits re-queue the job for the next slot
  without consuming a retry attempt.
- **Analytics polling** — at most once per
  `X_WEB_ANALYTICS_MIN_INTERVAL_HOURS` (6h).

To reset a breaker after fixing the account manually:
`docker exec redis redis-cli -a "$REDIS_PASSWORD" DEL x_web:guard`.

## Environment variables

Read by `social-api`, `social-worker-publishing`, `social-worker-default`
(and the other workers via the shared `x-worker-env` anchor) from the WSL
checkout's `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `X_WEB_FALLBACK_ENABLED` | `false` | master switch |
| `X_WEB_AUTH_TOKEN` | — | `auth_token` cookie (secret) |
| `X_WEB_CT0` | — | `ct0` cookie (secret) |
| `X_WEB_COOKIES_JSON` | — | optional full cookie export (secret) |
| `X_WEB_PROXY` | — | optional proxy for x.com requests |
| `X_WEB_MAX_POSTS_PER_DAY` | `5` | rolling-24h cap |
| `X_WEB_MIN_GAP_MINUTES` / `X_WEB_MAX_GAP_MINUTES` | `10` / `30` | random gap between posts |
| `X_WEB_BREAKER_HOURS` | `6` | breaker duration |
| `X_WEB_ANALYTICS_MIN_INTERVAL_HOURS` | `6` | analytics polling floor |
| `X_WEB_ANALYTICS_MAX_TWEET_LOOKUPS` | `10` | per-cycle tweet-detail lookups |
| `X_WEB_STATE_FILE` | `/app/uploads/.x_web_state.json` | guard state when Redis is down |
