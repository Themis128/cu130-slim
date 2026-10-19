---
name: slack-ops
description: >
  Slack ops tooling for SocialAuto — scripts/slack_tool.py health-checks all
  SLACK_* transports (webhook dead/live probes, auth.test, channel list,
  history) and upload_file_to_slack() adds the files.getUploadURLExternal
  flow. Use for Slack digest/alert delivery failures, dead webhook triage
  (404 no_service), missing bot-token scope checks, or attaching files to
  Slack messages.
---

# Slack ops

Slack in SocialAuto is **notification transport**, not a social platform —
digests, alerts, session-heal buttons, billing/paddle reports all POST to
per-channel incoming webhooks with a bot-token (`chat.postMessage`)
fallback. This skill covers ops + the file-upload gap.

## Tool

```bash
docker exec social-api python3 /app/scripts/slack_tool.py <cmd>
```

- `config` — every `SLACK_*` env: set/EMPTY, webhook URLs masked
- `webhooks` — **empty-POST probe** on every webhook URL. `400 no_text` =
  live (empty payload never posts); `404 no_service` = dead — the Slack app
  was uninstalled or the hook revoked, regenerate at
  api.slack.com/apps → Incoming Webhooks. This is the first thing to run
  when digests stop arriving.
- `auth` — `auth.test` on `SLACK_BOT_TOKEN`/`SLACK_ACCESS_TOKEN`
- `channels` — `conversations.list` (needs `channels:read`)
- `history <channel_id>` — `conversations.history` (needs `channels:history`)

Related: `scripts/ops_api.py` — admin login + TOTP (pulled from the user's
DB row) + call any `/api/v1` endpoint. Use it to replay the daily digest
(`POST /api/v1/ops/daily-digest?days=1&post_to_slack=true`) or verify a
fix without waiting for the 09:00 cron.

## Live state (verified 2026-10-17, important)

- **ALL configured webhook URLs return `404 no_service`** — alerts, billing,
  leads, publishing, support, and the default hook are all dead. Every
  Slack post currently rides the token fallback — a **single point of
  failure**. Regenerate incoming webhooks in the Cloudless Slack app and
  update `.env` (runbook below).
- **`SLACK_BOT_TOKEN` is now set and works** — it holds an `xoxe` **user**
  token (`bot=baltzakis.themis`, U09AF5VU7LY) synced by
  `slack_token_sync.py`; posts arrive authored by the user, not an app.
  Still aim for a real `xoxb-` bot token (manifest in `scripts/`).
- **Stale-env pitfall**: `chat.postMessage` → `channel_not_found` after an
  `.env` Slack-var change means a container is still running the OLD env —
  compose injects env only at create time. Recreate **every** token
  consumer (social-api + 4 workers + beat) — `slack_token_sync.py` already
  does this. Seen 2026-10-15/16 when the daily digest silently failed two
  mornings running.

## File uploads — `upload_file_to_slack()`

`app/services/slack_notifications.py` now has:

```python
ok, file_id, err = await upload_file_to_slack(
    content=bytes, filename="digest.png", channel_id="C0C1F1K3DDF",
    title="...", initial_comment="...", mime="image/png", thread_ts=None,
)
```

3-step external-upload flow (`files.getUploadURLExternal` → raw PUT →
`files.completeUploadExternal`) — the only supported path since Slack
sunset `files.upload` (Nov 2025). Requires `SLACK_BOT_TOKEN` + `files:write`
scope; **webhooks cannot upload files**. Fails soft `(False, None, err)` —
callers log and continue.

## Env inventory (container)

`SLACK_WEBHOOK_URL` (+`SLACK_CHANNEL_ID` = #socialauto), `SLACK_ALERTS_*`,
`SLACK_PUBLISHING_*`, `SLACK_LEADS_*`, `SLACK_SUPPORT_*`, `SLACK_BILLING_*`,
`SLACK_PADDLE_*` (webhook EMPTY), `SLACK_ADS_*`, `SLACK_APPROVALS_*`,
`SLACK_DIGEST_HOUR`, `SLACK_BILLING_DIGEST_HOUR`, `SLACK_PADDLE_DIGEST_HOUR`.


## Rebuilding after webhook revocation (2026-10-10 incident)

All six `SLACK_*_WEBHOOK_URL`s returned `404 no_service` and `SLACK_BOT_TOKEN`
was empty — the underlying Slack app had been deleted/revoked. Recovery:

1. Recreate the app: https://api.slack.com/apps → Create New App →
   **From a manifest** → paste `scripts/slack-app-manifest.yaml` (bundled
   with this skill — scopes match every Web API call the backend makes).
2. Install to workspace → copy `xoxb-` bot token → `SLACK_BOT_TOKEN` in `.env`.
3. App page → **Incoming Webhooks** → toggle ON → "Add New Webhook to
   Workspace" once per channel (`#socialauto`, `#socialauto-alerts`,
   `#socialauto-publishing`, `#socialauto-leads`, `#socialauto-approvals`,
   `#socialauto-support`, `#socialauto-billing`) → paste each URL into the
   matching `SLACK_*_WEBHOOK_URL`.
4. `docker compose up -d social-api` then verify:
   `python3 /app/scripts/slack_tool.py` → all probes `live`.

With `SLACK_BOT_TOKEN` set, digest delivery uses `chat.postMessage` and no
longer depends on webhooks at all — dead webhooks can't silently kill it.


## xoxe fallback via mcp-remote session (slack_token_sync.py)

When the app reinstall is pending and webhooks are still dead, the hosted
Slack MCP's stored `xoxe` user token (`~/.mcp-auth/mcp-remote-v1/*_tokens.json`,
scope `chat:write`) works as `SLACK_BOT_TOKEN` — `auth.test` + `chat.postMessage`
verified live against `wwwcloudlessgr` (T09AF5VTK4G). `xoxe` tokens last 12h
and rotate a refresh_token, so `scripts/slack_token_sync.py` refreshes via
`oauth.v2.user.access`, writes rotated tokens back to the mcp-remote file
(keeps the MCP server in sync, mode 0600), updates `.env`, and recreates
**every long-lived SLACK_BOT_TOKEN consumer** — social-api, the four Celery
workers and celery-beat (compose injects the token at startup; recreating
only social-api leaves worker digests on the expired token). If any compose
recreation fails the script exits nonzero and drops
`/var/tmp/slack_token_sync.pending`; the next run retries the recreation
even when the token is unchanged. Install the 2h cron idempotently with
`python3 scripts/slack_token_sync.py --install-cron` (writes
`0 */2 * * *` → `/var/tmp/slack_token_sync.log`).
`post_publishing_to_slack` now shares the fallback: webhook first, then
SLACK_BOT_TOKEN + SLACK_PUBLISHING_CHANNEL_ID (default: SLACK_CHANNEL_ID).
Replacing it with a real `xoxb` bot token (manifest above) removes the need
for the sync entirely.

## Related

- `publish-ops` — digest alert triage (platform-limit vs session vs config)
- cloudless.gr `slack-socialauto-actions` — interactive button handlers
  live on the website side, not the backend
- Skipped repos: `slack-sdk`/`bolt-python` — the httpx client already covers
  the needed surface; bolt would only matter for a Socket-Mode bot, and
  interactivity is handled by cloudless.gr endpoints.

## Known-broken: publish-completion notify (seen 2026-10-19)

Publish-completion notifications fail end-to-end: `Slack publishing webhook
dead (404) — falling back to bot token` then `Slack API error:
channel_not_found`. The daily-digest channel `C0BT263L17U` still works, so
this is the *publish-notify* path's channel config — the bot isn't a member
of (or the env points at a renamed/deleted) channel. Fix: resolve the actual
channel id via `slack_tool.py` channel list, update the env, or invite the
bot. Tracked in `.devin/BACKLOG.md` until fixed.
