# Publish preflight & maintenance window

Two ops tools that answer "will the scheduled posts actually publish?" and
"how do I hold the browser bridge for manual work?"

## publish_preflight.py — scheduled-post verification

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

### Reading the output

- `account status=expired` — the OAuth token is dead (preflight verifies it,
  not just the flag). Reconnect via SocialAuto Accounts page (OAuth) or the
  platform's noVNC login, then re-run. **Instagram exception:** IG publishing
  uses the web-API session (`private_api_session_id`/`private_api_csrf_token`/
  `private_api_ds_user_id` in `meta_data`), not the OAuth token — the flag can
  be stale either way. If the bridge's IG session is live (feed loads, not the
  login form), refresh meta_data from it:

  1. `maintenance_window.sh start` (or grab the bridge between pollers)
  2. `POST :9223/session/start {"platform":"instagram","force":true,
     "interactive":true}` then `POST /session/extract` (X-Platform: instagram)
  3. Update `social_accounts.meta_data`: `private_api_session_id` =
     `encrypt_field(sessionid)` (encrypted), `private_api_csrf_token` =
     csrftoken **plaintext** (publisher reads it raw), `private_api_ds_user_id`
     = ds_user_id plaintext; set `status='active'`
  4. `POST /session/stop`, `maintenance_window.sh stop`, re-run preflight

  Verify `ds_user_id` from the extract matches the account before writing —
  a mismatched session posts to the wrong profile. instagrapi login is not a
  fallback: it hits the "version out of date" wall.
- `token expires before schedule, no refresh token` — reconnect before the
  slot or the publish will fail.
- `URL unreachable` — R2/storage link is broken; regenerate or re-attach media.
- `no media attached` — text-only post; the strategy report flags these.

## maintenance_window.sh — bridge contention control

```bash
scripts/maintenance_window.sh start   # pause poller fleet (beat + 4 workers)
scripts/maintenance_window.sh stop    # resume everything
scripts/maintenance_window.sh status  # paused containers + bridge owner
```

Use `start` before manual Campaign Manager / profile edits through the
bridge (:9223) or a sidecar — otherwise scheduled pollers rotate in and
hijack the session mid-edit (409 busy loops). social-api stays up; only the
task fleet pauses. **Always `stop` afterwards** — a paused fleet silently
skips scheduled posts.

If the bridge still reports busy right after `start`, an in-flight poller
holds the busy-hold — wait ~30s and retry, or `POST /session/start` with
`{"platform": "<p>", "force": true}` for true emergencies.

## Related

- `scripts/session_health.py` — token/bridge/sidecar health matrix
- `scripts/publish_queue.py` — queue row inspection and stuck-lock recovery
- `scripts/session_transplant.py` — move a live session between browsers
- `.devin/skills/publish-queue-ops` — queue lifecycle and error signatures
- `.devin/skills/post-media-correctness` — media rules the preflight enforces
