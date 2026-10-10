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

## Live state (verified 2026-10, important)

- **ALL configured webhook URLs return `404 no_service`** — alerts, billing,
  leads, publishing, support, and the default hook are all dead; digests
  have been silently no-opping. Regenerate incoming webhooks in the
  Cloudless Slack app and update `.env`.
- **`SLACK_BOT_TOKEN` is EMPTY in the container** — the bot-token fallback
  in `_post_slack_text` can never fire. Set a `xoxb-` token to restore the
  fallback AND enable file uploads.

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

## Related

- `publish-ops` — digest alert triage (platform-limit vs session vs config)
- cloudless.gr `slack-socialauto-actions` — interactive button handlers
  live on the website side, not the backend
- Skipped repos: `slack-sdk`/`bolt-python` — the httpx client already covers
  the needed surface; bolt would only matter for a Socket-Mode bot, and
  interactivity is handled by cloudless.gr endpoints.
